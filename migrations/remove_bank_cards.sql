-- Run once on the application database after all old app workers have stopped
-- and the version without card management has initialized its tables.
BEGIN;

DELETE FROM activity_log
WHERE action IN (
    'Thêm thẻ ngân hàng',
    'Sửa thẻ ngân hàng',
    'Xoá thẻ ngân hàng',
    'Thêm dịch vụ vào thẻ',
    'Xoá dịch vụ khỏi thẻ',
    'Lấy thẻ cho dịch vụ'
);

DROP TABLE IF EXISTS card_service;
DROP TABLE IF EXISTS bank_card;

COMMIT;
