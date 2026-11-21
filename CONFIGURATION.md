# Cấu hình và khởi tạo quản trị viên

Với bản cài mới trên Docker Compose, sao chép `.env.example` thành `.env` cạnh `docker-compose.yml`. Đặt `SECRET_KEY`, `ADMIN_PASSWORD` và `POSTGRES_PASSWORD` thành ba bí mật riêng rồi chạy `docker compose up -d --build`. Ứng dụng dùng `POSTGRES_PASSWORD` để kết nối tới dịch vụ `db`; khi dùng PostgreSQL bên ngoài, ứng dụng ưu tiên `DATABASE_URL`. Khi triển khai ngoài Compose, truyền các biến này qua môi trường của tiến trình hoặc bộ quản lý bí mật của nền tảng.

Khi nâng cấp bản Compose cũ có volume PostgreSQL, thêm `POSTGRES_PASSWORD` vào cấu hình với mật khẩu hiện hành của role `appuser`. PostgreSQL chỉ dùng biến này để khởi tạo volume mới. Để thay mật khẩu của bản đang chạy, đổi mật khẩu role qua kênh quản trị PostgreSQL và cập nhật cả `POSTGRES_PASSWORD` cùng `DATABASE_URL` nếu dùng. Giữ bản cấu hình riêng của môi trường ở ngoài Git trước khi cập nhật bản triển khai.

Trên cơ sở dữ liệu trống, ứng dụng tạo quản trị viên từ `ADMIN_USERNAME` và `ADMIN_PASSWORD`. Những lần khởi động sau giữ nguyên tài khoản đang có. Sau khi khởi tạo thành công, có thể bỏ `ADMIN_PASSWORD` khỏi cấu hình. Để đổi mật khẩu một quản trị viên hiện có, chạy lệnh sau từ máy quản trị và nhập mật khẩu mới ở lời nhắc ẩn.

```sh
docker compose exec web flask --app app reset-admin-password TEN_QUAN_TRI
```

Nếu bí mật đã từng xuất hiện trong Git, image hoặc log, hãy thay các giá trị đang được sử dụng. Đổi `SECRET_KEY` sẽ làm hết hiệu lực các phiên đăng nhập hiện tại. Bản sao và lịch sử Git cũ có thể vẫn chứa giá trị cũ.
