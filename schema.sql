-- 팀 편성 앱의 테이블 (모두 tf_ 로 시작)
-- 앱이 시작할 때 이 파일을 그대로 실행하므로, 앱이 쓰는 테이블·열·제약과 이 파일이 같다.
-- 여러 번 실행해도 안전하도록 if not exists 를 쓴다.

-- 학생과 관리자. 비밀번호는 bcrypt 해시로만 저장한다.
create table if not exists tf_students (
    id            serial primary key,
    student_no    text not null unique,
    name          text not null,
    main_role     text check (main_role in ('기획자', '웹 프로그래머', '프론트엔드 디자이너', '테스터', '보고서 작성자', '발표자')),
    password_hash text not null,
    is_admin      boolean not null default false,
    -- 관리자가 아닌 학생은 주 역할이 반드시 있어야 함
    check (is_admin or main_role is not null)
);

-- 보조 역할. 학생 1명에 0~2행
create table if not exists tf_student_sub_roles (
    student_id integer not null references tf_students(id) on delete cascade,
    role       text not null check (role in ('기획자', '웹 프로그래머', '프론트엔드 디자이너', '테스터', '보고서 작성자', '발표자')),
    primary key (student_id, role)
);

-- 팀. id 가 곧 팀 번호(팀1 → 1)이며 화면의 team_1, apply_1 과 같다.
create table if not exists tf_teams (
    id   integer primary key,
    name text not null unique
);

-- 팀원.
-- (team_id, slot_no) 기본 키: 같은 자리에 두 명이 동시에 들어가면 먼저 도착한 학생만 기록됨
-- student_id 유일: 한 학생은 한 팀에만 소속
-- slot_no 1~4: 다섯 번째 자리를 막음
create table if not exists tf_members (
    team_id    integer not null references tf_teams(id) on delete cascade,
    slot_no    integer not null check (slot_no between 1 and 4),
    student_id integer not null unique references tf_students(id) on delete cascade,
    joined_at  timestamptz not null default now(),
    primary key (team_id, slot_no)
);

-- 지원
create table if not exists tf_applications (
    id          serial primary key,
    team_id     integer not null references tf_teams(id) on delete cascade,
    student_id  integer not null references tf_students(id) on delete cascade,
    status      text not null check (status in ('대기', '승인', '거절')),
    reason      text,
    created_at  timestamptz not null default now(),
    decided_at  timestamptz
);
-- 한 학생은 결과를 기다리는 지원을 하나만 가질 수 있음
create unique index if not exists tf_applications_one_pending
    on tf_applications (student_id) where status = '대기';

-- 투표. 한 팀원은 한 지원에 한 번만 투표(바꿀 수 없음)
create table if not exists tf_votes (
    application_id integer not null references tf_applications(id) on delete cascade,
    voter_id       integer not null references tf_students(id) on delete cascade,
    vote           text not null check (vote in ('찬성', '거부')),
    created_at     timestamptz not null default now(),
    primary key (application_id, voter_id)
);

-- 마감 여부, 배정안, 배정 진행 안내 등
create table if not exists tf_settings (
    key   text primary key,
    value text
);

-- AI 호출 기록(어느 모델이 왜 실패했는지 화면과 README 사례의 근거로 씀)
create table if not exists tf_ai_logs (
    id          serial primary key,
    run_id      text not null,
    attempt     integer not null,
    model       text not null,
    outcome     text not null,
    elapsed_sec numeric(6, 1),
    detail      text,
    created_at  timestamptz not null default now()
);
