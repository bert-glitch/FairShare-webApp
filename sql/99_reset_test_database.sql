
--
-- !! This DELETES everything in fairshare_test. It never touches the real "fairshare"
-- !! database. Run it only when you deliberately want a clean test database, then run
-- !! files 00-07 again with every "fairshare" replaced by "fairshare_test"
-- !! (the automated tests do this for you: python scripts/init_db.py --database fairshare_test).
-- =====================================================================
DROP DATABASE IF EXISTS fairshare_test;
