# Video Merger — bản chạy trên GitHub Actions

Bản này render video **trên máy chủ GitHub** thay vì Hugging Face, nên **không bị sleep** khi làm video dài (30–60 phút vẫn ổn — trần mỗi lần chạy là ~6 giờ).

Không có giao diện bấm nút. Bạn **sửa 1 file `jobs.json`** (danh sách link + tuỳ chọn) → GitHub tự render → tự upload Dropbox → in link ra.

> Giao diện Streamlit trên Hugging Face vẫn giữ để **test video ngắn**. GitHub Actions lo phần **render dài**.

---

## Cài đặt 1 lần (khoảng 10 phút)

### Bước 1 — Tạo repo trên GitHub
1. Vào https://github.com/new
2. Đặt tên, ví dụ `video-render`.
3. Chọn **Public** (quan trọng: public thì Actions **miễn phí không giới hạn phút**; private chỉ có 2.000 phút/tháng).
4. Tạo xong, upload toàn bộ các file trong thư mục này lên (kéo-thả trong tab **Add file → Upload files**), giữ nguyên cấu trúc:

```
.github/workflows/render.yml
pipeline/engine.py
pipeline/dropbox_util.py
pipeline/run_jobs.py
requirements.txt
jobs.json
jobs.example.json
```

### Bước 2 — Thêm Dropbox Secrets (bí mật, không lộ ra ngoài)
Repo public nhưng **secret luôn được mã hoá**, không ai đọc được.

1. Trong repo → **Settings** → **Secrets and variables** → **Actions** → **New repository secret**.
2. Thêm lần lượt **3 secret** (đúng tên, viết HOA):

| Name | Value |
|------|-------|
| `DROPBOX_APP_KEY` | App Key của bạn |
| `DROPBOX_APP_SECRET` | App Secret |
| `DROPBOX_REFRESH_TOKEN` | Refresh Token |

> Lấy 3 giá trị này tại https://www.dropbox.com/developers/apps (giống hệt phần đăng nhập trong app Hugging Face).
> Nếu **bỏ qua bước này**, video vẫn render được — chỉ là không tự upload Dropbox, mà tải về từ mục **Artifacts** của lần chạy.

---

## Cách dùng hằng ngày

### 1. Sửa `jobs.json`
Mở `jobs.json` trong repo (bấm biểu tượng bút chì để sửa online), điền link của bạn. Xem `jobs.example.json` để biết đủ 4 loại job.

### 2. Chạy
Có 2 cách, chọn 1:

- **Tự động**: chỉ cần **Commit** thay đổi `jobs.json` → workflow tự chạy.
- **Bấm nút**: tab **Actions** → chọn **Render Videos** → **Run workflow**.

### 3. Lấy kết quả
- Nếu đã cấu hình Dropbox: link hiện trong **log** của lần chạy, và file nằm trong thư mục Dropbox bạn khai báo.
- Luôn có bản dự phòng: tab **Actions** → mở lần chạy → cuối trang mục **Artifacts** → tải `rendered-videos`.

---

## 4 loại job (điền vào `"type"`)

| type | Làm gì | Trường chính |
|------|--------|--------------|
| `merge` | Ghép nhiều video/ảnh + nhiều audio → **1 video** | `videos[]`, `audios[]` |
| `concat` | Nối nhiều video nối tiếp, **giữ audio gốc** từng video | `videos[]` |
| `bulk` | Mỗi cặp (video/ảnh + audio) → **1 video riêng** (ra nhiều file) | `pairs[]` |
| `marketing` | Ảnh + giọng đọc (+ nhạc nền) → video dọc/ngang | `images[]`, `voice`, `music` |

Tuỳ chọn dùng chung:
- `resolution`: `"original"` | `"youtube"` (1280×720) | `"tiktok"` (576×1024)
- `audio_mode`: `"replace"` (thay tiếng gốc) | `"mix"` (hoà trộn)
- `image_motion`: `"static"` (đứng yên) | `"kenburns"` (zoom nhẹ)
- `image_duration`: số giây mỗi ảnh (nếu bỏ trống ở bulk/marketing → tự khớp độ dài audio)

Nhiều job có thể để chung trong 1 lần chạy — cứ thêm vào mảng `"jobs"`.

### Link nào dùng được?
Bất kỳ **direct link** nào tải thẳng ra file: Dropbox (tự đổi sang `dl=1`), Google Drive (`/file/d/...`), Cloudinary, CDN. Link chia sẻ dạng xem trước có thể không tải được — ưu tiên direct link `.mp4 / .jpg / .mp3`.

---

## Giới hạn cần biết
- **Public repo**: Actions miễn phí, không giới hạn phút. Mỗi lần chạy tối đa ~6 giờ.
- **Private repo**: 2.000 phút Linux/tháng miễn phí, hết thì dừng (mặc định không tự tính tiền).
- Artifacts giữ 7 ngày (đổi `retention-days` trong `render.yml` nếu muốn lâu hơn).
- Máy chủ Actions mạnh hơn Hugging Face free, nên render **thường nhanh hơn**.

## Chạy thử tại máy (tuỳ chọn)
```bash
pip install -r requirements.txt
# cần ffmpeg cài sẵn trên máy
python pipeline/run_jobs.py jobs.json
```
