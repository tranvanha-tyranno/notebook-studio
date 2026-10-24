# Notebook Studio

Web local để mở và quản lý JupyterLab trên GPU Modal. Chọn notebook từ bất kỳ thư mục nào, chọn tài khoản và GPU, rồi khởi động ngay trong trình duyệt.

## Khởi động trên Windows

Cần Python **3.11**, Git và tài khoản [Modal](https://modal.com). Node.js chỉ cần khi sửa extension, không cần để dùng app.

```powershell
git clone <URL-repo-này>
cd notebook-studio
.\modal.ps1 setup
python studio.py
```

Nếu PowerShell chặn script, dùng lệnh tương đương:

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-modal.txt
python studio.py
```

Trang tự mở tại **http://127.0.0.1:8765**. Giữ terminal chạy trong lúc làm việc. Các lần sau chỉ cần `python studio.py` hoặc `.\modal.ps1`.

1. Bấm **Tài khoản Modal** → đặt tên → **Đăng nhập bằng Modal**. Mở link xác thực, chọn đúng tài khoản/workspace và hoàn tất trên trang Modal. Các profile Modal CLI đã có sẽ tự xuất hiện.
2. Bấm **Chọn notebook…**, trỏ tới thư mục dự án, chọn `.ipynb`. Có thể thử `examples/gpu_check.ipynb` trước.
3. Chọn tài khoản, GPU, CPU, RAM, thời lượng; xem giá ước tính rồi bấm **Khởi động notebook**.
4. Khi trạng thái thành **Đang chạy**, bấm **Mở notebook**. Chọn cell và nhấn **Shift+Enter** để chạy.
5. Lưu bằng **Ctrl+S**, bấm **Dừng phiên** trên trang local khi làm xong.

Đóng tab không dừng GPU. Ctrl+C ở terminal sẽ dừng server local và các phiên do tiến trình đó mở. Phiên có giới hạn 1–240 phút; chi phí gồm GPU, CPU, RAM và các phí khác theo Modal. Khởi động app không tự tạo GPU; chỉ nút khởi động notebook mới tạo phiên.

## Tính năng

- GPU T4, L4, A10G, A100 40 GB và H100; giá GPU/CPU/RAM, thời lượng và ước tính compute theo phiên.
- Nhiều dự án và nhiều tài khoản đồng thời. Mỗi phiên giữ cố định tài khoản cho chạy, chuyển GPU, dừng app và lấy file.
- Notebook rộng, chế độ tập trung, thời gian cell gần nhất, thời gian phiên, CPU/RAM/GPU/VRAM trong bảng thông số riêng.
- Cây thư mục, bộ lọc, quản lý file ở khung lớn, copy đường dẫn tuyệt đối hoặc chuỗi Python, tải file/thư mục.
- **Lưu file về máy** trên web local: đọc Volume qua SDK, lưu trực tiếp vào `modal-downloads/` của dự án. Không phụ thuộc tính năng download của trình duyệt. File cũ không bị ghi đè; có manifest SHA-256.
- Thêm thư viện Python và gắn Data Volume của tài khoản đang chọn. Có sẵn Python 3.11, PyTorch 2.5.1/CUDA 12.4, torchvision, NumPy, pandas, Pillow và JupyterLab 4.4.10.

## File, dữ liệu và checkpoint

Chỉ notebook đã chọn và file `.py` trong dự án (nếu bật tùy chọn) được gửi lên; không tự tải dataset, `.env`, thư mục ẩn, môi trường ảo hay file kết quả local.

Notebook và kết quả lưu riêng theo dự án trong Volume `notebook-studio`, đường dẫn `interactive/projects/<project-key>/`. Mở lại mặc định giữ bản đã sửa trên cloud. **Dùng bản local mới nhất** tạo bản sao lưu cloud trước khi thay thế. Volume thuộc workspace tài khoản đang dùng.

Muốn dùng dataset, tạo/upload vào một Modal Volume rồi điền tên ở **Data Volume có sẵn**; Volume này xuất hiện tại `/mnt/data`. Đường dẫn trên máy Windows không tồn tại trong GPU Linux. Hãy sửa cell tương ứng trong notebook. Notebook có thể đọc thư mục hiện tại hoặc biến `CA2_OUTPUT_ROOT` để ghi kết quả vào thư mục `results/` bền vững. `/tmp`, RAM và file ngoài Volume không được lưu sau khi container dừng.

Lưu Ctrl+S và chờ khoảng 15 giây để đồng bộ trước khi lấy file về máy. Đổi GPU lưu file trước khi tạo server mới; model và biến trong RAM cần nạp lại. Notebook có thư viện/CLI riêng cần cài thêm trong tùy chọn môi trường hoặc trong cell.

## Nhiều tài khoản

**Dùng mặc định** chỉ đổi lựa chọn trong Studio, không đổi profile mặc định của Modal CLI. Các profile chung workspace dùng chung Volume và tiền; cùng dự án không được mở đồng thời bằng hai profile đã xác minh chung workspace.

Đăng xuất yêu cầu dừng phiên/chờ lưu file xong. Profile do Studio tạo sẽ bị xóa credentials local; profile CLI có sẵn chỉ bị ngắt khỏi Studio và có thể kết nối lại. Thao tác này không đăng xuất website Modal hay thu hồi token trên máy khác.

Metadata nằm tại `%LOCALAPPDATA%\NotebookStudio\`. Credentials của các lần đăng nhập mới nằm trong thư mục `accounts/` riêng, không trả token secret cho giao diện. Không chia sẻ thư mục dữ liệu local này. Repo không chứa credentials, dataset hay notebook của người dùng.

## Tiền từng tài khoản

Chi phí tháng, credits đã áp dụng và số tiền sau điều chỉnh lấy từ API chính thức [Workspace.billing.summary](https://modal.com/docs/sdk/py/latest/Workspace#billingsummary), tự cập nhật mỗi 5 phút hoặc qua **Cập nhật tiền**. Dữ liệu tính cho toàn workspace, có thể có độ trễ; số sau điều chỉnh chưa phải hóa đơn cuối kỳ.

API hiện không cung cấp số credits còn lại. Bấm **Xem trên Modal**, chọn đúng workspace, rồi **Nhập số dư** đang thấy. Studio ghi nhận mốc và trừ credits phát sinh tiếp theo, luôn ghi rõ **ước tính**. Cập nhật mốc khi nhận thêm credits/đổi gói; sang kỳ mới hoặc khi credits bị tính lại, giao diện yêu cầu nhập lại. Không tự giả định tài khoản nào cũng được $30. Lỗi mạng giữ dữ liệu cũ kèm thời điểm, không đổi thành $0.

## Phát triển và kiểm tra

```powershell
.\modal.ps1 test
node --check ui/launcher/app.js
```

Test chạy offline, sử dụng credentials giả trong thư mục tạm, không khởi tạo GPU. Đã kèm extension build sẵn. Khi sửa extension, cần Node.js 20+:

```powershell
cd ui/studio-extension
npm ci
npm run build
```

Commit cả source và kết quả `ui/jupyter/labextension/`. Web launcher nằm trong `ui/launcher/`, backend local trong `studio*.py`, môi trường cloud trong `modal_runtime.py` và `modal_lab.py`. Các tên nội bộ `CA2_*`/`@ca2/studio` được giữ để tương thích extension; app không phụ thuộc thư mục CA2 hay TACVU.

Nếu cổng bận: `python studio.py --port 8766`. Nếu mất mạng hoặc khởi động lỗi, xem **log khởi động & thông tin phiên** dưới thẻ phiên. Tài khoản không đủ quyền Billing vẫn có thể chạy notebook nếu có quyền compute.
