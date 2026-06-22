# 🔷 Polygon Uploader

> Tool tự động upload hàng loạt bài tập lập trình lên [Polygon](https://polygon.codeforces.com) — dành cho giáo viên và người ra đề.

![Python](https://img.shields.io/badge/Python-3.10+-3776AB?logo=python&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-0.115+-009688?logo=fastapi&logoColor=white)
![Gemini](https://img.shields.io/badge/Gemini-Vision_AI-4285F4?logo=google&logoColor=white)

---

## ✨ Tính năng

- 📄 **Parse đề tự động** — Upload file PDF, DOCX, hoặc ảnh → Gemini AI đọc và trích xuất đề bài thành LaTeX
- ✏️ **Preview & Edit** — Xem trước đề bài với KaTeX render, chỉnh sửa trước khi upload
- 🚀 **Batch upload** — Upload hàng loạt bài lên Polygon qua API chính thức
- 📁 **Auto-detect** — Tự scan thư mục, detect file đề, solution, và test cases
- 📊 **Real-time log** — Theo dõi tiến trình upload trực tiếp trên giao diện

---

## 📋 Yêu cầu

- **Python 3.10** trở lên
- **Polygon API key** — Lấy tại [polygon.codeforces.com](https://polygon.codeforces.com) → Settings → API
- **Gemini API key** — Lấy tại [Google AI Studio](https://aistudio.google.com/apikey)

---

## 🚀 Cài đặt & Chạy

### Windows

```bash
# 1. Clone hoặc download project
cd polygon-uploader

# 2. Chạy setup (cài dependencies)
setup.bat

# 3. Cấu hình API keys
#    Mở file .env và điền các key:
#    - POLYGON_API_KEY=...
#    - POLYGON_SECRET=...
#    - GEMINI_API_KEY=...

# 4. Chạy tool
run.bat
#    → Tự mở browser tại http://localhost:8080
```

### Mac / Linux

```bash
# 1. Cài dependencies
pip install -r requirements.txt

# 2. Copy và cấu hình .env
cp .env.example .env
# Mở .env, điền API keys

# 3. Chạy server
uvicorn main:app --host 0.0.0.0 --port 8080 --reload
# Mở browser: http://localhost:8080
```

---

## 📁 Chuẩn bị bài tập

Tổ chức bài theo cấu trúc thư mục. **Mỗi bài 1 folder**, chứa file đề và (tùy chọn) solution + tests:

```
problems/
├── bai1/
│   └── de.pdf                 ← file đề (bắt buộc)
│
├── bai2/
│   ├── bai2.docx              ← file đề
│   └── solution.cpp           ← solution (tùy chọn)
│
├── bai3/
│   ├── scan.png               ← file đề dạng ảnh
│   ├── solution.py            ← solution (tùy chọn)
│   └── tests/                 ← test cases (tùy chọn)
│       ├── 1                  ← input test 1
│       ├── 1.a                ← output test 1
│       ├── 2
│       └── 2.a
```

### Quy tắc

| Thành phần | Quy tắc |
|------------|---------|
| **File đề** | `.pdf`, `.docx`, `.doc`, `.png`, `.jpg`, `.jpeg` — mỗi folder 1 file |
| **Solution** | Tên `solution.*` hoặc `sol.*` (`.cpp`, `.py`, `.java`, `.c`) |
| **Tests** | Thư mục `tests/` chứa file đánh số: `1`, `1.a`, `2`, `2.a`, ... |
| **Tên folder** | Tùy ý — tool đặt tên Polygon theo cấu hình Level + Contest |

---

## 🖥️ Hướng dẫn sử dụng

### Bước 1: Cấu hình (Config)

1. Mở `http://localhost:8080`
2. Điền **Polygon API Key** và **Secret**
3. Điền **Gemini API Key**
4. Chọn **Level** (lv1, lv2, ...) và **Contest name** (array, dp, ...)
5. Dùng **File Browser** để chọn thư mục chứa bài
6. Kiểm tra danh sách bài detected
7. Nhấn **🚀 BẮT ĐẦU PARSE**

### Bước 2: Preview & Edit

1. Xem đề bài đã parse — bên trái là LaTeX, bên phải là preview KaTeX
2. Chỉnh sửa nếu cần:
   - Tên bài (`polygon_name`)
   - Time / Memory limit
   - Checker (mặc định `std::wcmp`)
   - Nội dung statement, input/output format
3. Nhấn **✅ Confirm** để xác nhận hoặc **⏭ Skip** để bỏ qua
4. Hoặc nhấn **⚡ Upload tất cả** để skip preview các bài còn lại

### Bước 3: Upload & Progress

1. Theo dõi tiến trình upload real-time
2. Mỗi bài hiển thị: ✅ thành công, 🔄 đang upload, ❌ lỗi
3. Log chi tiết từng bước API call
4. Khi hoàn tất, kiểm tra bài trên [polygon.codeforces.com](https://polygon.codeforces.com)

---

## ⚙️ Cấu hình nâng cao

### File `.env`

```env
POLYGON_API_KEY=your_polygon_api_key
POLYGON_SECRET=your_polygon_secret
GEMINI_API_KEY=your_gemini_api_key
```

### Naming convention

Tên bài trên Polygon được tạo tự động theo pattern:

```
{level} - {contest_name} - {index:02d}
```

Ví dụ: `lv1 - array - 01`, `lv1 - array - 02`, `lv2 - dp - 01`

### Checkers có sẵn

| Checker | Mô tả |
|---------|--------|
| `std::wcmp` | So sánh từng token (mặc định) |
| `std::ncmp` | So sánh số nguyên |
| `std::fcmp` | So sánh file chính xác |
| `std::rcmp4` | So sánh số thực (sai số 10⁻⁴) |
| `std::rcmp6` | So sánh số thực (sai số 10⁻⁶) |
| `std::rcmp9` | So sánh số thực (sai số 10⁻⁹) |
| `std::yesno` | So sánh YES/NO |

---

## 🏗️ Cấu trúc project

```
polygon-uploader/
├── main.py                     # FastAPI server + routes
├── modules/
│   ├── parser/
│   │   ├── models.py           # Pydantic models (Problem, Example)
│   │   ├── file_loader.py      # PDF/DOCX/Image → Gemini-ready
│   │   └── gemini_parser.py    # Gemini Vision API parser
│   ├── polygon/
│   │   ├── auth.py             # HMAC-SHA512 signature
│   │   ├── client.py           # Polygon REST API client
│   │   └── uploader.py         # Upload orchestration (7 steps)
│   └── batch/
│       └── scanner.py          # Folder scanner
├── frontend/
│   ├── index.html              # Config screen
│   ├── preview.html            # Preview & Edit screen
│   ├── progress.html           # Progress & Log screen
│   └── style.css               # Shared theme
├── .env.example
├── requirements.txt
├── setup.bat
└── run.bat
```

---

## 🔌 Mở rộng trong tương lai

| Module | Kế hoạch |
|--------|----------|
| `file_loader.py` | Thêm format: markdown, html, zip |
| `gemini_parser.py` | Swap model: GPT-4V, Claude Vision |
| `uploader.py` | Upload validator, generator, stress test |
| `scanner.py` | Config per-problem từ `config.yaml` |
| `frontend/` | Quản lý / list problem đã upload |
| `modules/` | Module `generator/` sinh test tự động |

---

## 🐛 Troubleshooting

| Vấn đề | Giải pháp |
|--------|-----------|
| `Gemini parse sai` | Chỉnh sửa thủ công trong Preview screen |
| `Polygon API error` | Kiểm tra API key/secret, đảm bảo có quyền tạo problem |
| `File đề không detect` | Đảm bảo extension đúng (.pdf, .docx, .png, .jpg) |
| `Time limit error` | Phải chia hết cho 50, nằm trong 250–15000ms |
| `Import error` | Chạy lại `setup.bat` hoặc `pip install -r requirements.txt` |

---

## 📄 License

MIT License — Sử dụng tự do cho mục đích giáo dục.
