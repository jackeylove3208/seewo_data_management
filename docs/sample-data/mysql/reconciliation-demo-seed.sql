-- Synthetic MySQL reconciliation demo data.
-- This script replaces the contents of the two demo organization_people tables.
-- Run it with a MySQL administrator account because authority_reader is intentionally read-only.

START TRANSACTION;

DELETE FROM authority_db.organization_people;
DELETE FROM seewo_db.organization_people;

INSERT INTO authority_db.organization_people (
    id,
    row_version,
    category,
    name,
    number,
    class_name,
    phone,
    email
)
WITH RECURSIVE student_sequence (sequence_number) AS (
    SELECT 1
    UNION ALL
    SELECT sequence_number + 1
    FROM student_sequence
    WHERE sequence_number < 80
)
SELECT
    CONCAT('student-', LPAD(sequence_number, 3, '0')),
    CONCAT('authority-seed-v1-', LPAD(sequence_number, 3, '0')),
    'student',
    CONCAT('测试学生', LPAD(sequence_number, 3, '0')),
    CONCAT('S2026', LPAD(sequence_number, 4, '0')),
    CONCAT('一年级', MOD(sequence_number - 1, 4) + 1, '班'),
    CONCAT('138', LPAD(sequence_number, 8, '0')),
    CONCAT('student', LPAD(sequence_number, 3, '0'), '@authority.example.test')
FROM student_sequence;

INSERT INTO authority_db.organization_people (
    id,
    row_version,
    category,
    name,
    number,
    class_name,
    phone,
    email
)
WITH RECURSIVE teacher_sequence (sequence_number) AS (
    SELECT 1
    UNION ALL
    SELECT sequence_number + 1
    FROM teacher_sequence
    WHERE sequence_number < 15
)
SELECT
    CONCAT('teacher-', LPAD(sequence_number, 3, '0')),
    CONCAT('authority-teacher-v1-', LPAD(sequence_number, 3, '0')),
    'teacher',
    CONCAT('测试教师', LPAD(sequence_number, 3, '0')),
    CONCAT('T2026', LPAD(sequence_number, 4, '0')),
    NULL,
    CONCAT('139', LPAD(sequence_number, 8, '0')),
    CONCAT('teacher', LPAD(sequence_number, 3, '0'), '@authority.example.test')
FROM teacher_sequence;

INSERT INTO authority_db.organization_people (
    id,
    row_version,
    category,
    name,
    number,
    class_name,
    phone,
    email
)
WITH RECURSIVE department_sequence (sequence_number) AS (
    SELECT 1
    UNION ALL
    SELECT sequence_number + 1
    FROM department_sequence
    WHERE sequence_number < 5
)
SELECT
    CONCAT('department-', LPAD(sequence_number, 3, '0')),
    CONCAT('authority-department-v1-', LPAD(sequence_number, 3, '0')),
    'department',
    CONCAT('测试部门', LPAD(sequence_number, 3, '0')),
    CONCAT('D2026', LPAD(sequence_number, 4, '0')),
    NULL,
    CONCAT('137', LPAD(sequence_number, 8, '0')),
    CONCAT('department', LPAD(sequence_number, 3, '0'), '@authority.example.test')
FROM department_sequence;

-- The target contains students 001-078. Students 079 and 080 are intentionally missing.
INSERT INTO seewo_db.organization_people (
    id,
    row_version,
    category,
    name,
    number,
    class_name,
    phone,
    email
)
WITH RECURSIVE student_sequence (sequence_number) AS (
    SELECT 1
    UNION ALL
    SELECT sequence_number + 1
    FROM student_sequence
    WHERE sequence_number < 78
)
SELECT
    CONCAT('student-', LPAD(sequence_number, 3, '0')),
    CONCAT('seewo-seed-v1-', LPAD(sequence_number, 3, '0')),
    'student',
    CASE sequence_number
        WHEN 21 THEN '测试学牲021'
        WHEN 22 THEN '测试学笙022'
        ELSE CONCAT('测试学生', LPAD(sequence_number, 3, '0'))
    END,
    CASE sequence_number
        WHEN 31 THEN 'S20269931'
        WHEN 32 THEN 'S20269932'
        ELSE CONCAT('S2026', LPAD(sequence_number, 4, '0'))
    END,
    CONCAT('一年级', MOD(sequence_number - 1, 4) + 1, '班'),
    CONCAT('138', LPAD(sequence_number, 8, '0')),
    CONCAT('student', LPAD(sequence_number, 3, '0'), '@authority.example.test')
FROM student_sequence;

-- Students 081 and 082 exist only in Seewo and are intentionally extra.
INSERT INTO seewo_db.organization_people (
    id,
    row_version,
    category,
    name,
    number,
    class_name,
    phone,
    email
)
VALUES
    (
        'student-081',
        'seewo-extra-v1-081',
        'student',
        '希沃多余学生081',
        'S20260081',
        '一年级1班',
        '13800000081',
        'student081@seewo-extra.example.test'
    ),
    (
        'student-082',
        'seewo-extra-v1-082',
        'student',
        '希沃多余学生082',
        'S20260082',
        '一年级2班',
        '13800000082',
        'student082@seewo-extra.example.test'
    );

INSERT INTO seewo_db.organization_people (
    id,
    row_version,
    category,
    name,
    number,
    class_name,
    phone,
    email
)
WITH RECURSIVE teacher_sequence (sequence_number) AS (
    SELECT 1
    UNION ALL
    SELECT sequence_number + 1
    FROM teacher_sequence
    WHERE sequence_number < 15
)
SELECT
    CONCAT('teacher-', LPAD(sequence_number, 3, '0')),
    CONCAT('seewo-teacher-v1-', LPAD(sequence_number, 3, '0')),
    'teacher',
    CONCAT('测试教师', LPAD(sequence_number, 3, '0')),
    CONCAT('T2026', LPAD(sequence_number, 4, '0')),
    NULL,
    CONCAT('139', LPAD(sequence_number, 8, '0')),
    CONCAT('teacher', LPAD(sequence_number, 3, '0'), '@authority.example.test')
FROM teacher_sequence;

INSERT INTO seewo_db.organization_people (
    id,
    row_version,
    category,
    name,
    number,
    class_name,
    phone,
    email
)
WITH RECURSIVE department_sequence (sequence_number) AS (
    SELECT 1
    UNION ALL
    SELECT sequence_number + 1
    FROM department_sequence
    WHERE sequence_number < 5
)
SELECT
    CONCAT('department-', LPAD(sequence_number, 3, '0')),
    CONCAT('seewo-department-v1-', LPAD(sequence_number, 3, '0')),
    'department',
    CONCAT('测试部门', LPAD(sequence_number, 3, '0')),
    CONCAT('D2026', LPAD(sequence_number, 4, '0')),
    NULL,
    CONCAT('137', LPAD(sequence_number, 8, '0')),
    CONCAT('department', LPAD(sequence_number, 3, '0'), '@authority.example.test')
FROM department_sequence;

COMMIT;

SELECT 'authority_total' AS metric, COUNT(*) AS value
FROM authority_db.organization_people
UNION ALL
SELECT 'seewo_total', COUNT(*)
FROM seewo_db.organization_people;

SELECT
    'name_mismatch' AS expected_issue,
    authority.id,
    authority.name AS authority_value,
    seewo.name AS seewo_value
FROM authority_db.organization_people AS authority
JOIN seewo_db.organization_people AS seewo ON seewo.id = authority.id
WHERE authority.id IN ('student-021', 'student-022')
UNION ALL
SELECT
    'number_mismatch',
    authority.id,
    authority.number,
    seewo.number
FROM authority_db.organization_people AS authority
JOIN seewo_db.organization_people AS seewo ON seewo.id = authority.id
WHERE authority.id IN ('student-031', 'student-032')
UNION ALL
SELECT
    'seewo_missing',
    authority.id,
    authority.name,
    NULL
FROM authority_db.organization_people AS authority
LEFT JOIN seewo_db.organization_people AS seewo ON seewo.id = authority.id
WHERE authority.id IN ('student-079', 'student-080')
  AND seewo.id IS NULL
UNION ALL
SELECT
    'seewo_extra',
    seewo.id,
    NULL,
    seewo.name
FROM seewo_db.organization_people AS seewo
LEFT JOIN authority_db.organization_people AS authority ON authority.id = seewo.id
WHERE seewo.id IN ('student-081', 'student-082')
  AND authority.id IS NULL
ORDER BY expected_issue, id;
