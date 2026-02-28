-- ============================================================================
-- PARKWIZ ANPR Service — Capture Log Table
-- ============================================================================
-- Run this script ONCE against your PARKWIZ database to create the logging
-- table used by the ANPR service.
--
-- Usage:
--   sqlcmd -S PW\V2019 -d PARKWIZ -i create_capture_log.sql
-- ============================================================================

IF NOT EXISTS (
    SELECT * FROM INFORMATION_SCHEMA.TABLES
    WHERE TABLE_NAME = 'tblANPRCaptureLog'
)
BEGIN
    CREATE TABLE tblANPRCaptureLog (
        LogID           BIGINT IDENTITY(1,1) PRIMARY KEY,
        PMSLaneNumber   VARCHAR(10)   NOT NULL,
        ANPROrgID       VARCHAR(50)   NOT NULL,
        CameraIP        VARCHAR(50),
        PlateDetected   VARCHAR(20),
        RawOCRText      VARCHAR(100),
        Confidence      DECIMAL(5,4),
        DetectionMethod VARCHAR(50),
        ProcessingMs    INT,
        ErrorCode       VARCHAR(50),
        ImagePath       VARCHAR(500),
        CapturedAt      DATETIME2     DEFAULT GETUTCDATE(),
        RequestID       VARCHAR(50)
    );

    PRINT 'Table tblANPRCaptureLog created successfully.';
END
ELSE
BEGIN
    PRINT 'Table tblANPRCaptureLog already exists — skipping.';
END
GO

-- Performance indexes
IF NOT EXISTS (
    SELECT * FROM sys.indexes
    WHERE name = 'IX_ANPRLog_Lane'
    AND object_id = OBJECT_ID('tblANPRCaptureLog')
)
BEGIN
    CREATE INDEX IX_ANPRLog_Lane
    ON tblANPRCaptureLog (PMSLaneNumber, CapturedAt DESC);
    PRINT 'Index IX_ANPRLog_Lane created.';
END
GO

IF NOT EXISTS (
    SELECT * FROM sys.indexes
    WHERE name = 'IX_ANPRLog_Plate'
    AND object_id = OBJECT_ID('tblANPRCaptureLog')
)
BEGIN
    CREATE INDEX IX_ANPRLog_Plate
    ON tblANPRCaptureLog (PlateDetected, CapturedAt DESC);
    PRINT 'Index IX_ANPRLog_Plate created.';
END
GO

PRINT 'ANPR Capture Log setup complete.';
GO
