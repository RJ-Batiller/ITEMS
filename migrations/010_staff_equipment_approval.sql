ALTER TABLE roles
    MODIFY name VARCHAR(50) NOT NULL UNIQUE;

UPDATE roles
SET name = 'Staff', description = 'Can submit equipment for administrative approval'
WHERE name = 'Worker';

ALTER TABLE equipment
    ADD COLUMN approval_status ENUM('Pending','Approved','Rejected') NOT NULL DEFAULT 'Approved' AFTER status,
    ADD COLUMN created_by INT NULL AFTER approval_status,
    ADD COLUMN reviewed_by INT NULL AFTER created_by,
    ADD COLUMN reviewed_at DATETIME NULL AFTER reviewed_by,
    ADD COLUMN review_details VARCHAR(500) NULL AFTER reviewed_at,
    ADD INDEX idx_equipment_approval_status (approval_status),
    ADD INDEX idx_equipment_created_by (created_by),
    ADD CONSTRAINT fk_equipment_created_by FOREIGN KEY (created_by) REFERENCES users(id) ON DELETE SET NULL,
    ADD CONSTRAINT fk_equipment_reviewed_by FOREIGN KEY (reviewed_by) REFERENCES users(id) ON DELETE SET NULL;
