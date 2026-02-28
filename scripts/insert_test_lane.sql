-- Script to insert test lane L26
IF NOT EXISTS (
    SELECT * FROM INFORMATION_SCHEMA.TABLES
    WHERE TABLE_NAME = 'tblLaneANPRConfiguration'
)
BEGIN
    CREATE TABLE tblLaneANPRConfiguration (
        ANPRID INT IDENTITY(1,1) PRIMARY KEY,
        PMSLaneNumber VARCHAR(20) NOT NULL,
        flgEnableANPR BIT NOT NULL DEFAULT 1,
        ANPROrgID VARCHAR(50) NOT NULL,
        ANPRLaneID VARCHAR(50),
        ANPRPublicKey VARCHAR(255),
        ANPRPrivateKey VARCHAR(255),
        ANPRSource VARCHAR(50),
        ANPRAPIURL VARCHAR(255) NOT NULL,
        ANPRAPIURL2 VARCHAR(255),
        ActiveStatus CHAR(1) NOT NULL DEFAULT 'Y'
    );
    PRINT 'Table tblLaneANPRConfiguration created successfully.';
END
ELSE
BEGIN
    PRINT 'Table tblLaneANPRConfiguration already exists.';
END
GO

-- Insert or Update Lane 26
IF EXISTS (SELECT 1 FROM tblLaneANPRConfiguration WHERE PMSLaneNumber = '26' AND ANPROrgID = 'PARKWIZ')
BEGIN
    UPDATE tblLaneANPRConfiguration
    SET ANPRAPIURL = '192.168.1.64',
        flgEnableANPR = 1,
        ActiveStatus = 'Y'
    WHERE PMSLaneNumber = '26' AND ANPROrgID = 'PARKWIZ';
    PRINT 'Lane 26 updated successfully.';
END
ELSE
BEGIN
    INSERT INTO tblLaneANPRConfiguration (PMSLaneNumber, flgEnableANPR, ANPROrgID, ANPRAPIURL, ActiveStatus)
    VALUES ('26', 1, 'PARKWIZ', '192.168.1.64', 'Y');
    PRINT 'Lane 26 inserted successfully.';
END
GO
