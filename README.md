# 🔷 AutoCF — Polygon Uploader

> Tool tự động parse đề bài và upload hàng loạt lên [Polygon (Codeforces)](https://polygon.codeforces.com) — dành cho giáo viên và người ra đề.

![Python](https://img.shields.io/badge/Python-3.10+-3776AB?logo=python&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-0.115+-009688?logo=fastapi&logoColor=white)
![Groq](https://img.shields.io/badge/Groq-Vision_AI-F55036?logo=groq&logoColor=white)

---

## ✨ Tính năng

- 📄 **Parse đề tự động** — Upload file PDF, DOCX, hoặc ảnh → Groq Vision AI đọc và trích xuất đề thành LaTeX
- ✏️ **Preview & Edit** — Xem trước toàn bộ đề dạng Polygon (title, limits, statement, I/O, examples), chỉnh sửa trực tiếp
- 🤖 **AI gen solution & test** — Tự động sinh solution C++ và test generator cho từng subtask, preview trước khi upload
- 🚀 **Batch upload** — Upload hàng loạt lên Polygon qua API chính thức
- 📁 **Auto-detect** — Tự scan thư mục, nhận diện file đề, solution sẵn có, và test cases
- 📊 **Real-time log** — Theo dõi tiến trình upload từng bước

---

## 📋 Yêu cầu

| Thứ | Lấy ở đâu |
|-----|-----------|
| **Python 3.10+** | [python.org](https://www.python.org/downloads/) |
| **Polygon API key + Secret** | [polygon.codeforces.com](https://polygon.codeforces.com) → Settings → API |
| **Groq API key** | [console.groq.com](https://console.groq.com) → API Keys (miễn phí, 14,400 req/ngày) |
| **testlib.h** *(chỉ cần nếu dùng Gen Test)* | [github.com/MikeMirzayanov/testlib](https://github.com/MikeMirzayanov/testlib) → download `testlib.h` |
| **MinGW / g++** *(chỉ cần nếu dùng Gen Test)* | Windows: [winlibs.com](https://winlibs.com) — Linux/Mac: `gcc` có sẵn |

---

## ⚙️ Cài đặt

### 1. Clone repo

```bash
git clone https://github.com/MinhCYB/AutoCF.git
cd AutoCF
```

### 2. Cài dependencies

**Windows:**
```bash
setup.bat
```

**Linux / macOS:**
```bash
pip install -r requirements.txt
```

### 3. Cấu hình `.env`

Copy file mẫu và điền thông tin:

```bash
# Windows
copy .env.example .env

# Linux / macOS
cp .env.example .env
```

Mở `.env` và điền:

```env
# ── Bắt buộc ──────────────────────────────────────────
POLYGON_API_KEY=your_polygon_api_key
POLYGON_SECRET=your_polygon_secret
GROQ_API_KEY=your_groq_api_key

# ── Tùy chọn ──────────────────────────────────────────
# Đường dẫn tới testlib.h (bắt buộc nếu muốn dùng Gen Test)
TESTLIB_PATH=C:\testlib\testlib.h
# Linux / macOS:
# TESTLIB_PATH=/home/user/testlib/testlib.h

# Delay giữa các lần parse (giây) — tránh rate limit Groq
PARSE_DELAY=15

# Tự động gen solution C++ khi upload (true/false)
GEN_SOLUTION=false

# Tự động gen test generator C++ khi upload (true/false)
GEN_TESTS=false
```

> **Lấy Polygon API key:** Đăng nhập [polygon.codeforces.com](https://polygon.codeforces.com) → góc trên phải → **Settings** → tab **API** → **Add API key**

> **Lấy Groq API key:** Đăng ký tại [console.groq.com](https://console.groq.com) → **API Keys** → **Create API Key**

---

## 🚀 Chạy

**Windows:**
```bash
run.bat
```

**Linux / macOS:**
```bash
uvicorn main:app --host 0.0.0.0 --port 8080 --reload
```

Sau đó mở browser tại **http://localhost:8080**

---

## 📁 Chuẩn bị bài tập

Tổ chức bài theo cấu trúc thư mục — **mỗi bài 1 folder**:

```
problems/
├── bai1/
│   └── de.pdf                  ← file đề (bắt buộc)
│
├── bai2/
│   ├── bai2.docx               ← file đề
│   └── solution.cpp            ← solution có sẵn (tùy chọn)
│
├── bai3/
│   ├── scan.png                ← file đề dạng ảnh
│   ├── solution.cpp
│   └── tests/                  ← test cases có sẵn (tùy chọn)
│       ├── 1                   ← input test 1
│       ├── 1.a                 ← output test 1
│       ├── 2
│       └── 2.a
```

| Thành phần | Quy tắc |
|------------|---------|
| **File đề** | `.pdf`, `.docx`, `.doc`, `.png`, `.jpg`, `.jpeg` — mỗi folder 1 file |
| **Solution** | Tên chứa `solution` hoặc `sol` (`.cpp`, `.py`, `.java`, `.c`) |
| **Tests** | Thư mục `tests/` — input: `1`, `2`, ... — output: `1.a`, `2.a`, ... |
| **Tên folder** | Tùy ý — Polygon name được tạo tự động theo Level + Contest |

---

## 🖥️ Hướng dẫn sử dụng

### Bước 1 — Config

1. Mở **http://localhost:8080**
2. Cấu hình trong phần **AI Config**:
   - **Delay giữa các bài** — tăng nếu gặp rate limit (mặc định 15s)
   - **Gen Solution C++** — tự động gen solution khi parse xong
   - **Gen Test Generator C++** — tự động gen test (cần `testlib.h`)
3. Điền **Contest name**, **Level**, **Start index** để đặt tên Polygon tự động
4. Dùng **File Browser** chọn thư mục chứa bài → click **Scan**
5. Xem danh sách bài được detect, nhấn **🚀 Bắt đầu Parse**

> Tên bài Polygon được tạo theo pattern: `{level} - {contest} - {index:02d}`  
> Ví dụ: `lv1 - array - 01`, `lv1 - array - 02`

### Bước 2 — Preview & Edit

Sau khi parse, tool tự động chuyển sang trang Preview:

- **Cột trái** — chỉnh sửa toàn bộ: Problem Settings, Statement, Input/Output Format, Examples, Gen Solution, Gen Test
- **Cột phải** — preview đề kiểu Polygon: title, time/memory limit, statement LaTeX, bảng examples (cập nhật realtime)

**Các thao tác chính:**

| Nút | Tác dụng |
|-----|----------|
| **✅ Confirm** | Xác nhận bài, chuyển sang bài tiếp |
| **⏭ Skip** | Bỏ qua bài này |
| **⚡ Upload tất cả** | Upload ngay, bỏ qua preview các bài còn lại |
| **💡 Gen Solution** | Gen lại solution C++ bằng AI |
| **⚡ Gen Test** | Gen lại test từ subtask hiện tại |
| **✏️ Sửa subtask** | Quay về textarea để chỉnh subtask |
| **💾 Lưu** | Lưu solution đã sửa vào server |

**Gen Test — subtask format:**

Mỗi dòng trong textarea subtask theo cú pháp:
```
score | constraints | n_tests
```
Ví dụ:
```
20 | 1 ≤ n ≤ 100 | 5
30 | 1 ≤ n ≤ 1000 | 5
50 | 1 ≤ n ≤ 100000 | 10
```

Nếu đề không có subtask, AI tự đề xuất 1 subtask 100 điểm dựa trên constraints trong đề.

### Bước 3 — Upload & Progress

1. Sau khi confirm hết bài → nhấn **Upload** (hoặc **⚡ Upload tất cả** ở Preview)
2. Theo dõi log real-time — từng bước API call hiển thị rõ ràng
3. Mỗi bài qua 7 bước: tạo problem → update info → save statement → upload examples → upload solution → upload tests → commit
4. Khi xong → kiểm tra tại [polygon.codeforces.com](https://polygon.codeforces.com)

---

## 🏗️ Cấu trúc project

```
AutoCF/
├── main.py                      # FastAPI server + toàn bộ API routes
├── modules/
│   ├── parser/
│   │   ├── models.py            # Pydantic models: Problem, Example, Subtask
│   │   ├── file_loader.py       # PDF/DOCX/Image → nội dung cho Groq
│   │   ├── groq_parser.py       # Groq Vision — parse đề → LaTeX
│   │   └── groq_codegen.py      # Groq — gen solution, subtask, generator C++
│   ├── polygon/
│   │   ├── auth.py              # HMAC-SHA512 signature cho Polygon API
│   │   ├── client.py            # Polygon REST API client
│   │   ├── uploader.py          # Orchestration upload 7 bước
│   │   └── test_runner.py       # Compile + chạy generator local
│   └── batch/
│       └── scanner.py           # Scan thư mục, detect file đề/solution/tests
├── frontend/
│   ├── index.html               # Màn hình Config & Scan
│   ├── preview.html             # Màn hình Preview & Edit
│   ├── progress.html            # Màn hình Progress & Log
│   └── style.css                # Theme chung (dark mode)
├── .env.example                 # Template cấu hình
├── requirements.txt
├── setup.bat                    # Cài dependencies (Windows)
└── run.bat                      # Chạy server (Windows)
```

---

## 🐛 Troubleshooting

| Vấn đề | Giải pháp |
|--------|-----------|
| `Chưa cấu hình Groq API key` | Kiểm tra `GROQ_API_KEY` trong `.env`, restart server |
| `Chưa cấu hình testlib_path` | Điền `TESTLIB_PATH` trong `.env` trỏ tới file `testlib.h` |
| `rate limit Groq` | Tăng `PARSE_DELAY` lên 20–30s trong `.env` |
| `Polygon API error 401` | Kiểm tra `POLYGON_API_KEY` và `POLYGON_SECRET` |
| `g++ not found` | Cài MinGW (Windows) hoặc `sudo apt install g++` (Linux) |
| `Parse sai nội dung` | Chỉnh sửa thủ công trong Preview screen |
| `Time limit error` | Phải là bội số của 50, nằm trong khoảng 250–15000ms |
| `Import error` | Chạy lại `setup.bat` hoặc `pip install -r requirements.txt` |

---

## 📄 License

MIT License — Sử dụng tự do cho mục đích giáo dục.