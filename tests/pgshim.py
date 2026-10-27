"""테스트 전용: psycopg 를 설치할 수 없는 환경에서 로컬 PostgreSQL 에 붙는 아주 작은 드라이버.

앱 코드(store.py)가 쓰는 만큼만 흉내 낸다: cursor().execute(sql, params), fetchall(),
description, rowcount, commit/rollback, 오류의 sqlstate. 배포 앱은 이 파일을 쓰지 않는다.
"""
from __future__ import annotations

import socket
import struct
from contextlib import contextmanager
from datetime import datetime


class PgError(Exception):
    def __init__(self, fields: dict):
        self.sqlstate = fields.get("C")
        super().__init__(f"{fields.get('C')}: {fields.get('M')}")


def _lit(v) -> str:
    if v is None:
        return "NULL"
    if isinstance(v, bool):
        return "TRUE" if v else "FALSE"
    if isinstance(v, (int, float)):
        return repr(v)
    return "'" + str(v).replace("'", "''") + "'"


def interpolate(sql: str, params) -> str:
    params = list(params or ())
    out, i, n = [], 0, 0
    while i < len(sql):
        if sql.startswith("%%", i):
            out.append("%"); i += 2
        elif sql.startswith("%s", i):
            out.append(_lit(params[n])); n += 1; i += 2
        else:
            out.append(sql[i]); i += 1
    assert n == len(params), "인자 수가 맞지 않음"
    return "".join(out)


class Conn:
    def __init__(self, host="127.0.0.1", port=5433, user="postgres", dbname="tf_test"):
        self.s = socket.create_connection((host, port))
        body = struct.pack("!i", 196608) + b"".join(
            k.encode() + b"\0" + v.encode() + b"\0" for k, v in (("user", user), ("database", dbname))) + b"\0"
        self.s.sendall(struct.pack("!i", len(body) + 4) + body)
        self._until_ready()
        self.in_tx = False

    def _recv(self, n):
        buf = b""
        while len(buf) < n:
            chunk = self.s.recv(n - len(buf))
            if not chunk:
                raise ConnectionError("연결 끊김")
            buf += chunk
        return buf

    def _msg(self):
        t = self._recv(1)
        ln = struct.unpack("!i", self._recv(4))[0]
        return t, self._recv(ln - 4)

    def _until_ready(self):
        while True:
            t, b = self._msg()
            if t == b"E":
                raise PgError(self._fields(b))
            if t == b"R" and struct.unpack("!i", b[:4])[0] != 0:
                raise RuntimeError("trust 인증만 지원")
            if t == b"Z":
                return

    @staticmethod
    def _fields(b):
        out = {}
        for part in b.split(b"\0"):
            if part:
                out[chr(part[0])] = part[1:].decode()
        return out

    def raw(self, sql: str):
        q = sql.encode() + b"\0"
        self.s.sendall(b"Q" + struct.pack("!i", len(q) + 4) + q)
        cols, data, count, err = None, [], -1, None
        while True:
            t, b = self._msg()
            if t == b"T":
                n = struct.unpack("!h", b[:2])[0]; pos = 2; cols = []
                for _ in range(n):
                    end = b.index(b"\0", pos); name = b[pos:end].decode(); pos = end + 1
                    oid = struct.unpack("!i", b[pos + 6:pos + 10])[0]; pos += 18
                    cols.append((name, oid))
                data = []
            elif t == b"D":
                n = struct.unpack("!h", b[:2])[0]; pos = 2; row = []
                for i in range(n):
                    ln = struct.unpack("!i", b[pos:pos + 4])[0]; pos += 4
                    if ln < 0:
                        row.append(None)
                    else:
                        row.append(self._conv(b[pos:pos + ln].decode(), cols[i][1])); pos += ln
                data.append(row)
            elif t == b"C":
                tag = b.rstrip(b"\0").decode().split()
                count = int(tag[-1]) if tag and tag[-1].isdigit() else -1
            elif t == b"E":
                err = PgError(self._fields(b))
            elif t == b"Z":
                if err:
                    raise err
                return cols, data, count

    @staticmethod
    def _conv(s, oid):
        if oid in (20, 21, 23):
            return int(s)
        if oid == 16:
            return s == "t"
        if oid in (700, 701, 1700):
            return float(s)
        if oid == 1184:
            return datetime.fromisoformat(s if len(s.split("+")[-1]) != 2 else s + ":00")
        return s

    def execute(self, sql, params=()):
        if not self.in_tx:
            self.raw("BEGIN"); self.in_tx = True
        return self.raw(interpolate(sql, params))

    def commit(self):
        if self.in_tx:
            self.raw("COMMIT"); self.in_tx = False

    def rollback(self):
        if self.in_tx:
            self.raw("ROLLBACK"); self.in_tx = False

    def cursor(self, row_factory=None):
        return Cursor(self)


class Cursor:
    def __init__(self, conn):
        self.conn, self.description, self._rows, self.rowcount = conn, None, [], -1

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params=()):
        cols, data, count = self.conn.execute(sql, params)
        self.description = cols
        self._rows = [dict(zip([c[0] for c in cols], r)) for r in data] if cols else []
        self.rowcount = count

    def fetchall(self):
        return self._rows


class Pool:
    """psycopg_pool.ConnectionPool.connection() 처럼 동작(끝나면 commit, 예외면 rollback)."""

    def __init__(self, **kw):
        self.kw = kw
        self.c = Conn(**kw)

    @contextmanager
    def connection(self):
        try:
            yield self.c
        except BaseException:
            self.c.rollback()
            raise
        else:
            self.c.commit()
