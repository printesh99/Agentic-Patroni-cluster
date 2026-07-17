\set ON_ERROR_STOP on

\connect object_monitor
create schema if not exists object_metrics;

create table if not exists object_metrics.appmon_replication_slots (
  slot_name text primary key,
  slot_type text not null,
  database_name text not null,
  active boolean not null,
  retained_wal_bytes bigint not null
);
truncate object_metrics.appmon_replication_slots;
insert into object_metrics.appmon_replication_slots values
  ('uat_core_logical_slot', 'logical', 'uat_core_banking', true, 73400320),
  ('uat_docs_archive_slot', 'logical', 'uat_documents', false, 18874368),
  ('uat_gateway_physical_slot', 'physical', '', true, 4194304);

create table if not exists object_metrics.appmon_subscriptions (
  datname text not null,
  subscription text primary key,
  publications text not null,
  enabled boolean not null
);
truncate object_metrics.appmon_subscriptions;
insert into object_metrics.appmon_subscriptions values
  ('uat_core_banking', 'sub_core_to_reporting', 'pub_postings,pub_accounts', true),
  ('uat_documents', 'sub_docs_to_archive', 'pub_documents', true),
  ('uat_gateway', 'sub_gateway_to_fraud', 'pub_api_events', false);

create table if not exists object_metrics.appmon_workers (
  datname text not null,
  backend_type text not null,
  application_name text not null,
  sessions integer not null
);
truncate object_metrics.appmon_workers;
insert into object_metrics.appmon_workers values
  ('uat_core_banking', 'logical replication launcher', 'core-apply-worker', 2),
  ('uat_documents', 'walsender', 'docs-archive-sender', 1),
  ('uat_gateway', 'logical replication worker', 'gateway-fraud-apply', 1);

\connect uat_customer
create schema if not exists crm_slim;
create table if not exists crm_slim.customers (
  customer_id integer primary key,
  segment text not null,
  status text not null,
  opened_at timestamptz not null default now()
);
truncate crm_slim.customers;
insert into crm_slim.customers(customer_id, segment, status, opened_at)
select gs,
       case when gs % 5 = 0 then 'SME' when gs % 3 = 0 then 'PRIORITY' else 'RETAIL' end,
       case when gs % 97 = 0 then 'REVIEW' else 'ACTIVE' end,
       now() - ((gs % 900) || ' days')::interval
from generate_series(1, 2400) gs;
analyze crm_slim.customers;
update crm_slim.customers set status = 'KYC_REVIEW' where customer_id <= 75;

\connect uat_core_banking
create schema if not exists tps;
create table if not exists tps.accounts (
  account_id integer primary key,
  customer_id integer not null,
  opened_at timestamptz not null,
  status text not null,
  balance numeric(16,2) not null
);
create table if not exists tps.posting_events (
  event_id bigserial primary key,
  account_id integer not null,
  event_type text not null,
  amount numeric(16,2) not null,
  event_ts timestamptz not null,
  channel text not null
);
create index if not exists idx_posting_events_ts on tps.posting_events(event_ts);
create index if not exists idx_posting_events_account on tps.posting_events(account_id);
truncate tps.posting_events, tps.accounts restart identity;
insert into tps.accounts(account_id, customer_id, opened_at, status, balance)
select gs, 100000 + gs,
       now() - ((gs % 730) || ' days')::interval,
       case when gs % 101 = 0 then 'WATCH' when gs % 43 = 0 then 'DORMANT' else 'ACTIVE' end,
       round((1000 + random() * 250000)::numeric, 2)
from generate_series(1, 2000) gs;
insert into tps.posting_events(account_id, event_type, amount, event_ts, channel)
select (gs % 2000) + 1,
       case when gs % 6 = 0 then 'REVERSAL'
            when gs % 5 = 0 then 'FEE'
            when gs % 4 = 0 then 'TRANSFER'
            when gs % 3 = 0 then 'BILLPAY'
            else 'POSTING' end,
       round((25 + random() * 9000)::numeric, 2),
       now() - ((gs % 72) || ' minutes')::interval,
       case when gs % 4 = 0 then 'MOBILE' when gs % 3 = 0 then 'GATEWAY' when gs % 2 = 0 then 'BRANCH' else 'ATM' end
from generate_series(1, 6500) gs;

create schema if not exists tps_warehouse;
create table if not exists tps_warehouse.hourly_posting_summary (
  bucket_ts timestamptz not null,
  event_type text not null,
  event_count integer not null,
  amount_total numeric(16,2) not null,
  primary key(bucket_ts, event_type)
);
truncate tps_warehouse.hourly_posting_summary;
insert into tps_warehouse.hourly_posting_summary(bucket_ts, event_type, event_count, amount_total)
select date_trunc('hour', now()) - (h || ' hours')::interval,
       v.event_type,
       100 + h * 3 + length(v.event_type),
       round(((100 + h * 3 + length(v.event_type)) * (500 + random() * 2000))::numeric, 2)
from generate_series(0, 23) h
cross join (values ('POSTING'), ('TRANSFER'), ('BILLPAY'), ('FEE')) as v(event_type);

create schema if not exists service;
create table if not exists service.service_calls (
  call_id bigserial primary key,
  service_name text not null,
  status text not null,
  latency_ms integer not null,
  created_at timestamptz not null
);
create index if not exists idx_service_calls_name on service.service_calls(service_name);
truncate service.service_calls restart identity;
insert into service.service_calls(service_name, status, latency_ms, created_at)
select case when gs % 4 = 0 then 'crm-profile'
            when gs % 3 = 0 then 'kafka-event-publisher'
            when gs % 2 = 0 then 'dashboard-api'
            else 'core-service' end,
       case when gs % 67 = 0 then 'ERROR' else 'OK' end,
       10 + (gs % 250),
       now() - ((gs % 3600) || ' seconds')::interval
from generate_series(1, 1800) gs;

create schema if not exists charge;
create table if not exists charge.charge_events (
  charge_id bigserial primary key,
  account_id integer not null,
  charge_type text not null,
  amount numeric(12,2) not null,
  created_at timestamptz not null
);
truncate charge.charge_events restart identity;
insert into charge.charge_events(account_id, charge_type, amount, created_at)
select (gs % 2000) + 1,
       case when gs % 3 = 0 then 'CARD_FEE' when gs % 2 = 0 then 'VAT' else 'SERVICE_CHARGE' end,
       round((2 + random() * 120)::numeric, 2),
       now() - ((gs % 48) || ' hours')::interval
from generate_series(1, 950) gs;

analyze tps.accounts;
analyze tps.posting_events;
analyze tps_warehouse.hourly_posting_summary;
analyze service.service_calls;
analyze charge.charge_events;
update tps.accounts set balance = balance + 10 where account_id <= 120;
delete from tps.posting_events where event_id <= 80;
insert into tps.posting_events(account_id, event_type, amount, event_ts, channel)
select (gs % 2000) + 1, 'POSTING', round((50 + random() * 500)::numeric, 2), now(), 'MOBILE'
from generate_series(1, 180) gs;

\connect uat_etl
create schema if not exists tps_warehouse;
create table if not exists tps_warehouse.etl_batches (
  batch_id bigserial primary key,
  pipeline_name text not null,
  rows_loaded integer not null,
  status text not null,
  completed_at timestamptz not null
);
truncate tps_warehouse.etl_batches restart identity;
insert into tps_warehouse.etl_batches(pipeline_name, rows_loaded, status, completed_at)
select case when gs % 2 = 0 then 'posting-hourly-rollup' else 'warehouse-reconciliation' end,
       5000 + gs * 37,
       case when gs % 19 = 0 then 'RETRY' else 'DONE' end,
       now() - ((gs % 72) || ' hours')::interval
from generate_series(1, 120) gs;
analyze tps_warehouse.etl_batches;
update tps_warehouse.etl_batches set rows_loaded = rows_loaded + 50 where batch_id <= 10;

\connect uat_gateway
create schema if not exists api_gateway;
create table if not exists api_gateway.events (
  event_id bigserial primary key,
  route text not null,
  status_code integer not null,
  latency_ms integer not null,
  event_ts timestamptz not null
);
create index if not exists idx_gateway_events_route on api_gateway.events(route);
truncate api_gateway.events restart identity;
insert into api_gateway.events(route, status_code, latency_ms, event_ts)
select case when gs % 4 = 0 then '/payments'
            when gs % 3 = 0 then '/accounts'
            when gs % 2 = 0 then '/documents'
            else '/customers' end,
       case when gs % 79 = 0 then 500 when gs % 29 = 0 then 429 else 200 end,
       20 + (gs % 450),
       now() - ((gs % 3600) || ' seconds')::interval
from generate_series(1, 1400) gs;
analyze api_gateway.events;
update api_gateway.events set latency_ms = latency_ms + 25 where event_id <= 60;

\connect uat_mobile
create schema if not exists mobile;
create table if not exists mobile.events (
  event_id bigserial primary key,
  device_os text not null,
  action text not null,
  status text not null,
  event_ts timestamptz not null
);
truncate mobile.events restart identity;
insert into mobile.events(device_os, action, status, event_ts)
select case when gs % 2 = 0 then 'iOS' else 'Android' end,
       case when gs % 4 = 0 then 'login' when gs % 3 = 0 then 'transfer' when gs % 2 = 0 then 'billpay' else 'balance' end,
       case when gs % 91 = 0 then 'FAIL' else 'OK' end,
       now() - ((gs % 7200) || ' seconds')::interval
from generate_series(1, 1600) gs;
analyze mobile.events;
update mobile.events set status = 'RETRY' where event_id <= 40;

\connect uat_locker
create schema if not exists locker;
create table if not exists locker.events (
  event_id bigserial primary key,
  locker_id integer not null,
  action text not null,
  status text not null,
  event_ts timestamptz not null
);
truncate locker.events restart identity;
insert into locker.events(locker_id, action, status, event_ts)
select (gs % 300) + 1,
       case when gs % 3 = 0 then 'unlock' when gs % 2 = 0 then 'reserve' else 'release' end,
       case when gs % 53 = 0 then 'EXCEPTION' else 'OK' end,
       now() - ((gs % 2400) || ' seconds')::interval
from generate_series(1, 700) gs;
analyze locker.events;
update locker.events set status = 'REVIEW' where event_id <= 25;

\connect uat_documents
create schema if not exists document;
create table if not exists document.events (
  event_id bigserial primary key,
  document_type text not null,
  action text not null,
  status text not null,
  event_ts timestamptz not null
);
create index if not exists idx_document_events_type on document.events(document_type);
truncate document.events restart identity;
insert into document.events(document_type, action, status, event_ts)
select case when gs % 4 = 0 then 'statement' when gs % 3 = 0 then 'kyc' when gs % 2 = 0 then 'loan' else 'receipt' end,
       case when gs % 3 = 0 then 'index' when gs % 2 = 0 then 'render' else 'archive' end,
       case when gs % 61 = 0 then 'ERROR' else 'OK' end,
       now() - ((gs % 5000) || ' seconds')::interval
from generate_series(1, 1000) gs;
analyze document.events;
delete from document.events where event_id <= 35;

\connect uat_payments
create schema if not exists charge;
create table if not exists charge.events (
  event_id bigserial primary key,
  payment_type text not null,
  amount numeric(12,2) not null,
  status text not null,
  event_ts timestamptz not null
);
truncate charge.events restart identity;
insert into charge.events(payment_type, amount, status, event_ts)
select case when gs % 3 = 0 then 'domestic' when gs % 2 = 0 then 'card' else 'swift' end,
       round((100 + random() * 20000)::numeric, 2),
       case when gs % 73 = 0 then 'REJECTED' else 'SETTLED' end,
       now() - ((gs % 6000) || ' seconds')::interval
from generate_series(1, 1300) gs;
analyze charge.events;
update charge.events set status = 'REVIEW' where event_id <= 45;

\connect uat_cards
create schema if not exists charge;
create table if not exists charge.card_authorizations (
  auth_id bigserial primary key,
  card_product text not null,
  amount numeric(12,2) not null,
  status text not null,
  event_ts timestamptz not null
);
truncate charge.card_authorizations restart identity;
insert into charge.card_authorizations(card_product, amount, status, event_ts)
select case when gs % 3 = 0 then 'credit' when gs % 2 = 0 then 'debit' else 'prepaid' end,
       round((10 + random() * 5000)::numeric, 2),
       case when gs % 41 = 0 then 'DECLINED' else 'APPROVED' end,
       now() - ((gs % 6000) || ' seconds')::interval
from generate_series(1, 1500) gs;
analyze charge.card_authorizations;
update charge.card_authorizations set status = 'REVIEW' where auth_id <= 50;

do $$
begin
  perform pg_stat_force_next_flush();
exception when undefined_function then
  null;
end $$;
