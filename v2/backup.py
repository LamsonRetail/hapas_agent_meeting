"""
Sao lưu `state.db` + khóa Fernet — tự động, chạy ngay trong vòng `run`.

Vì sao phải TỰ ĐỘNG chứ không phải một mục trong sổ tay: sổ tay §6 đã dặn
"backup cùng nhau nhưng để tách chỗ" từ đầu, và tới 02/08/2026 vẫn chưa có bản
backup nào tồn tại. Một quy trình dựa vào trí nhớ con người, bảo vệ thứ mà mất
là hỏng cả hệ thống, thì không phải là bảo vệ.

Mất `state.db` gây HAI thiệt hại, và cái thứ hai hay bị quên:
  1. Mọi user token biến mất -> cả công ty enroll lại.
  2. PHÂN QUYỀN HỎI ĐÁP biến mất. `qa.viewers_index()` dựng "ai được xem cuộc
     họp nào" từ `jobs.meta_json.attendees`. Base vẫn còn nguyên biên bản, nhưng
     không có job tương ứng thì `qa._may_see` trả False cho tất cả (trừ admin)
     — cả công ty mất quyền đọc biên bản của CHÍNH MÌNH, và không có lỗi nào
     hiện ra để đoán vì sao.

Ba quyết định thiết kế, đừng đảo lại mà không đọc:

  - **`VACUUM INTO`, không phải copy file.** DB chạy ở chế độ WAL và lúc backup
    thì tiến trình `run` đang ghi (file `-wal` đo được 3.2 MB). Copy `.db` mà
    bỏ `-wal` là chép về một bản THIẾU những gì vừa ghi, mà nó vẫn mở được nên
    không ai biết. `VACUUM INTO` đọc qua chính engine SQLite: kết quả là một
    file duy nhất, đã checkpoint, nhất quán về mặt giao dịch, không cần dừng
    tiến trình nào.

  - **Khóa Fernet để RIÊNG CHỖ.** DB không có key = vô dụng, key không có DB =
    vô dụng, nên chép cả hai vào cùng một thư mục là làm mất tác dụng của việc
    mã hóa mà không được thêm chút an toàn nào. Mặc định key nằm ngoài repo và
    ngoài `data/` (xem `config.KEY_BACKUP_PATH`).

  - **Không được làm chết vòng `run`.** Cùng lý lẽ với `alerts.check_all`: đĩa
    đầy hay thư mục bị khóa không phải lý do để dừng phát biên bản. Mọi lỗi ở
    đây đều được nuốt và in ra, `run` chạy tiếp.

⚠️ Giới hạn của cấu hình mặc định: trên máy này C:, D:, E: là ba PHÂN VÙNG của
MỘT ổ vật lý (đo 02/08/2026, Disk #0). Backup mặc định chống được DB corrupt,
xoá nhầm, migration hỏng — KHÔNG chống được chết ổ. Chống chết ổ thì phải đưa
bản sao ra khỏi máy, xem `V2_BACKUP_DIR` và §6 sổ tay.
"""

from __future__ import annotations

import hashlib
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import config

_TZ = timezone(timedelta(hours=7))
_PREFIX = "state-"
_SUFFIX = ".db"

# Đã backup lần này chưa, trong ĐỜI của tiến trình. Chỉ để khỏi stat thư mục mỗi
# 5 phút; mốc THẬT là mtime của file backup mới nhất, nên restart tiến trình
# không sinh ra một bản thừa.
_last_try = 0.0


def enabled() -> bool:
    return config.BACKUP_EVERY_HOURS > 0


def key_fingerprint(key: str = "") -> str:
    """Vân tay khóa Fernet — để đối chiếu key với DB mà KHÔNG lộ key.

    Dùng ở hai chỗ: ghi vào README cạnh bản backup (biết ngay bản này giải được
    bằng key nào), và phát hiện `V2_FERNET_KEY` bị đổi (xem `_write_key`).
    """
    k = key or config.FERNET_KEY
    if not k:
        return "(trống)"
    return hashlib.sha256(k.encode("utf-8")).hexdigest()[:16]


# ----------------------------------------------------------------- liệt kê


def snapshots() -> list[Path]:
    """Các bản backup đã có, MỚI NHẤT TRƯỚC."""
    d = config.BACKUP_DIR
    if not d.exists():
        return []
    files = [p for p in d.glob(f"{_PREFIX}*{_SUFFIX}") if p.is_file()]
    return sorted(files, key=lambda p: p.stat().st_mtime, reverse=True)


def latest() -> tuple[Path | None, float]:
    """(bản mới nhất, số GIỜ tuổi). (None, inf) nếu chưa có bản nào."""
    files = snapshots()
    if not files:
        return None, float("inf")
    return files[0], (time.time() - files[0].stat().st_mtime) / 3600


# ------------------------------------------------------------------- ghi


def _write_key() -> str:
    """Chép khóa Fernet ra `config.KEY_BACKUP_PATH`. Trả câu mô tả để in log.

    Phát hiện luôn tai nạn tệ nhất của hệ thống này: khóa đã lưu KHÁC khóa đang
    dùng, tức ai đó vừa đổi `V2_FERNET_KEY` trong khi đã có người enroll (một
    trong "ba điều không được làm sai" — V2_HANDOFF §5). Khi đó khóa cũ là thứ
    DUY NHẤT còn giải được các bản backup đã có, nên KHÔNG ghi đè: đổi tên nó
    thành `.prev-<vân tay>` rồi nói to.
    """
    if not config.FERNET_KEY:
        return "khóa Fernet TRỐNG — không có gì để lưu"
    dest = config.KEY_BACKUP_PATH
    dest.parent.mkdir(parents=True, exist_ok=True)
    fp = key_fingerprint()

    if dest.exists():
        old = ""
        for line in dest.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                old = line
                break
        if old == config.FERNET_KEY:
            return f"khóa đã có sẵn (vân tay {fp})"
        if old:
            prev = dest.with_suffix(f".prev-{key_fingerprint(old)}.txt")
            dest.replace(prev)
            print("!" * 60)
            print(f"[backup] V2_FERNET_KEY ĐÃ ĐỔI (cũ {key_fingerprint(old)} "
                  f"-> mới {fp}). Khóa CŨ giữ ở {prev} — đừng xoá, nó là thứ "
                  f"duy nhất giải được các bản backup trước đó.")
            print("!" * 60)

    dest.write_text(
        "# Khóa V2_FERNET_KEY của meetingxlark — giải mã token trong state.db.\n"
        f"# Vân tay: {fp}\n"
        f"# Ghi lúc: {datetime.now(_TZ):%Y-%m-%d %H:%M:%S} (+07)\n"
        "# Không có khóa này thì bản sao state.db là rác. Đừng để chung thư mục\n"
        "# với bản sao DB, và đừng đổi khóa khi đã có người enroll.\n"
        f"{config.FERNET_KEY}\n",
        encoding="utf-8")
    return f"khóa ghi ra {dest} (vân tay {fp})"


def _write_readme() -> None:
    """README cạnh bản backup: nói rõ thiếu gì thì bản sao này vô dụng."""
    (config.BACKUP_DIR / "README.txt").write_text(
        "Bản sao state.db của meetingxlark V2 (sinh tự động bởi v2/backup.py).\n"
        "\n"
        "PHỤC HỒI:\n"
        "  1. Dừng orchestrator (đóng cửa sổ `v2 run`, hoặc kill python.exe).\n"
        "  2. Copy state-<ngày>.db đè lên v2\\data\\state.db\n"
        "     và XOÁ hai file v2\\data\\state.db-wal, state.db-shm nếu còn.\n"
        f"  3. Đặt lại V2_FERNET_KEY trong v2\\.env đúng khóa có vân tay "
        f"{key_fingerprint()}\n"
        f"     (khóa được lưu ở: {config.KEY_BACKUP_PATH})\n"
        "  4. python -m v2 doctor   -> mục V2_FERNET_KEY phải [+]\n"
        "\n"
        "KHÔNG CÓ KHÓA thì các file .db ở đây là rác: token bên trong đã mã hóa\n"
        "bằng Fernet và không có đường nào giải khác.\n"
        "\n"
        "Bản sao này chứa recap nội dung cuộc họp ở dạng ĐỌC ĐƯỢC (chỉ token là\n"
        "mã hóa). Cân nhắc điều đó trước khi đồng bộ thư mục này lên cloud.\n",
        encoding="utf-8")


def run_backup() -> Path | None:
    """Tạo một bản sao ngay. Trả đường dẫn, hoặc None nếu hỏng.

    Ném ra ngoài KHÔNG bao giờ: caller là vòng `run` và lệnh CLI, cả hai đều
    không được chết vì backup.
    """
    try:
        config.BACKUP_DIR.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(_TZ).strftime("%Y-%m-%d-%H%M%S")
        dest = config.BACKUP_DIR / f"{_PREFIX}{stamp}{_SUFFIX}"
        if dest.exists():                    # cùng giây -> đừng để VACUUM ném
            dest.unlink()

        # Kết nối RIÊNG, chỉ để backup: `db.conn()` là handle dùng chung của
        # tiến trình và VACUUM không chạy được bên trong một giao dịch đang mở.
        con = sqlite3.connect(str(config.DB_PATH), timeout=30)
        try:
            con.execute("VACUUM INTO ?", (str(dest),))
        finally:
            con.close()

        mb = dest.stat().st_size / 1_048_576
        note = _write_key()
        _write_readme()
        removed = _rotate()
        print(f"[backup] {dest.name} ({mb:.1f} MB) · {note}"
              + (f" · dọn {removed} bản cũ" if removed else ""))
        return dest
    except Exception as exc:                 # noqa: BLE001 — xem docstring
        print(f"[backup] sao lưu HỎNG (bỏ qua, vòng run vẫn chạy): {exc}")
        return None


def _rotate() -> int:
    """Xoá bản cũ, giữ `BACKUP_KEEP` bản mới nhất. Trả số bản đã xoá."""
    keep = max(1, config.BACKUP_KEEP)
    old = snapshots()[keep:]
    n = 0
    for p in old:
        try:
            p.unlink()
            n += 1
        except OSError as exc:
            print(f"[backup] không xoá được bản cũ {p.name}: {exc}")
    return n


def maybe_backup() -> None:
    """Gọi mỗi vòng `run`. Chỉ thật sự backup khi bản mới nhất đã đủ già.

    Mốc là mtime của FILE, không phải biến trong RAM: restart tiến trình là
    chuyện thường (`run-v2-auto.bat` tự bật lại), và nếu đếm trong RAM thì mỗi
    lần khởi động lại là một bản thừa.
    """
    global _last_try
    if not enabled():
        return
    now = time.monotonic()
    if now - _last_try < 600:            # đừng stat thư mục mỗi 5 phút
        return
    _last_try = now
    _, age_h = latest()
    if age_h < config.BACKUP_EVERY_HOURS:
        return
    run_backup()
