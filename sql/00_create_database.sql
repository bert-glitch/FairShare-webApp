
CREATE DATABASE IF NOT EXISTS fairshare
    CHARACTER SET utf8mb4
    COLLATE utf8mb4_0900_ai_ci;
USE fairshare;

-- Stops with a clear message if the server is too old.
DROP PROCEDURE IF EXISTS tmp_check_version;
DELIMITER $$
CREATE PROCEDURE tmp_check_version()
BEGIN
    DECLARE v_ver  VARCHAR(40) DEFAULT SUBSTRING_INDEX(VERSION(), '-', 1);
    DECLARE v_maj  INT DEFAULT CAST(SUBSTRING_INDEX(v_ver, '.', 1) AS UNSIGNED);
    DECLARE v_min  INT DEFAULT CAST(SUBSTRING_INDEX(SUBSTRING_INDEX(v_ver, '.', 2), '.', -1) AS UNSIGNED);
    DECLARE v_pat  INT DEFAULT CAST(SUBSTRING_INDEX(v_ver, '.', -1) AS UNSIGNED);
    IF VERSION() LIKE '%MariaDB%' OR v_maj < 8 OR (v_maj = 8 AND v_min = 0 AND v_pat < 16) THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'FairShare needs MySQL 8.0.16 or newer (MariaDB is not supported).';
    END IF;
END$$
DELIMITER ;
CALL tmp_check_version();
DROP PROCEDURE tmp_check_version;

SELECT VERSION() AS mysql_version, @@event_scheduler AS event_scheduler, @@transaction_isolation AS isolation_level;
