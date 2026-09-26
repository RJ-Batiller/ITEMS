ALTER TABLE users
    ADD COLUMN profile_picture_data LONGBLOB NULL,
    ADD COLUMN profile_picture_mime VARCHAR(120) NULL;
