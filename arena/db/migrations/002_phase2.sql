-- Phase 2 additions. Safe to run more than once.
alter table articles   add column if not exists facts_extracted_at timestamptz;
alter table fact_cards add column if not exists published_at timestamptz;
alter table portfolios add column if not exists brake_active boolean not null default false;
alter table portfolios add column if not exists start_date date;
-- Practice mode (fake AI) is kept apart from live mode so placeholder facts never reach real teams.
alter table articles   add column if not exists practice_extracted_at timestamptz;
alter table fact_cards add column if not exists practice boolean not null default false;
create index if not exists fact_cards_published_at_idx on fact_cards (published_at);
create index if not exists articles_unprocessed_idx on articles (published_at) where facts_extracted_at is null;

create table if not exists leaderboard (
  key           text primary key,          -- 'team:<id>' or 'SPY'
  team_id       bigint references teams(id),
  name          text not null,
  is_benchmark  boolean not null default false,
  start_date    date,
  as_of         date,
  equity        double precision,
  total_return  double precision,
  sharpe        double precision,
  max_drawdown  double precision,
  trades        integer not null default 0,
  updated_at    timestamptz not null default now()
);
alter table leaderboard enable row level security;
revoke all on leaderboard from anon, authenticated;
grant all on leaderboard to service_role;
grant select on leaderboard to anon;
drop policy if exists "public read" on leaderboard;
create policy "public read" on leaderboard for select to anon using (true);
