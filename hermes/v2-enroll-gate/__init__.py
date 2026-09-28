"""Cửa vào bot biên bản họp: chưa cấp quyền OAuth thì không được vào agent.

Vì sao ở đây mà không ở tầng allowlist của Hermes: user muốn người mới **tự**
cấp quyền và bản ghi nằm trong DB của V2 (state.db), không phải DB của Hermes.
`FEISHU_ALLOW_ALL_USERS=true` mở cửa Hermes, plugin này là cửa thật.

Hook `pre_gateway_dispatch` chạy TRƯỚC cửa auth của Hermes (gateway/run.py) và
nhận `MessageEvent`. Trả về:
    {"action": "skip", "reason": ...}   -> bỏ tin, agent không thấy
    {"action": "allow"}                 -> đi tiếp
    {"action": "rewrite", "text": ...}  -> đổi nội dung tin rồi đi tiếp

VÉ PHIÊN (thêm 31/07/2026): khi cho vào, V2 trả kèm `asker_token` — vé định danh
người gửi. Plugin gắn nó vào `channel_prompt` tạm thời của đúng event để agent truyền
lại qua tham số `asker_token` của tool MCP. Không chèn vào user message: Hermes lưu
message vào session DB, còn channel prompt không được persist. Đó là cách duy nhất
`qa.py` biết ai đang hỏi:
MCP server là MỘT tiến trình dùng chung cho cả tenant, Hermes spawn nó một lần
với env tĩnh, nên lời gọi tool không mang danh tính. Xem `v2/askers.py`.

Không có vé thì vẫn `allow`: cửa fail-closed nằm ở tầng dữ liệu của V2 (một chỗ
duy nhất), không phải ở đây. Chặn ở đây nữa thì bot im mà không ai biết vì sao.

Định danh: `SessionSource.user_id_alt` = union_id của Feishu (theo chính chú thích
trong gateway/session.py) — đó cũng là khoá V2 lưu trong bảng `tokens`, nên khớp
được mà không phải tra danh bạ.

HỒ SƠ + KÝ ỨC (thêm 06/08/2026): cùng lời gọi gate đó nay trả thêm hai trường,
và plugin nối chúng vào `channel_prompt` cạnh vé phiên:
    profile — bot là ai, làm được gì, luồng chạy thế nào (`v2/profile.py`).
              Không có nó thì "bạn tên gì" rơi vào nhánh "phải gọi tool
              meetings" và bị chặn như một câu hỏi dữ liệu hỏng.
    memory  — cuộc họp người này VỪA nhắc tới (`v2/gate._memory_block`), đọc từ
              SQLite nên sống qua reset phiên và qua `hermes gateway restart` —
              hai chỗ mà lịch sử phiên của Hermes chết.
Cả hai đi channel_prompt vì cùng lý do với vé: không bị lưu vào session DB, nên
mỗi lượt là bản V2 vừa đọc, không phải bản chụp cũ nằm lại trong lịch sử.

CHỈ CHAT 1-1 (thêm 02/08/2026): tin trong group/channel/thread bị bỏ. Lý do
không phải "sợ agent lẫn vé của hai người" — Hermes để
`group_sessions_per_user: true` nên mỗi người trong group đã là một session
riêng. Lý do thật: bộ lọc của V2 cấp quyền cho **người HỎI**, còn câu trả lời
thì **cả phòng đọc được**, kể cả người không có trong danh sách người dự.

FAIL-CLOSED: gọi V2 hỏng, hết thời gian, JSON xấu -> `skip`. Bot im lặng an toàn
hơn bot trả lời người chưa cấp quyền. Log ở %LOCALAPPDATA%\\hermes\\logs\\gateway.log.
"""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import threading
import time
import unicodedata

logger = logging.getLogger(__name__)

# Đường dẫn launcher. Đổi được bằng env cho lúc di trú máy.
GATE_BAT = os.environ.get("V2_GATE_BAT", r"E:\meetingxlark\v2-gate.bat")

# Cửa chỉ áp cho Feishu/Lark. Các nền tảng khác (CLI, telegram…) không liên quan.
PLATFORM = "feishu"

TIMEOUT_S = float(os.environ.get("V2_GATE_TIMEOUT", "25"))

# Quy tắc này được gắn vào ``MessageEvent.channel_prompt`` ở MỖI lượt. Khác với
# ``platform_hints.feishu`` (Hermes chụp lại trong system_prompt của session),
# channel_prompt là system prompt tạm thời và không bị lưu cùng lịch sử. Vì vậy
# một session cũ vẫn nhận ngay chính sách mới sau khi gateway nạp lại plugin.
POLICY_VERSION = "meetingxlark-v2-safety-2026-09-28.1"
_POLICY = f"""[CHÍNH SÁCH HỆ THỐNG {POLICY_VERSION}]
Bạn là bot chuyên dụng cho dữ liệu cuộc họp MeetingxLark V2 trên Feishu.
- Với mọi yêu cầu về DỮ LIỆU cuộc họp, PHẢI gọi ít nhất một tool thuộc MCP
  `meetings` trong CHÍNH lượt này trước khi trả lời. Miễn trừ đúng ba loại:
  chào hỏi/cảm ơn; hỏi VỀ BẠN (tên, làm được gì, hoạt động thế nào); và hỏi
  cách dùng bot. Ba loại đó trả lời thẳng từ hồ sơ, đừng gọi tool cho có.
- Không dùng kết quả tool hoặc danh sách cuộc họp từ lượt cũ để trả lời lượt
  mới. Ngữ cảnh cũ chỉ dùng để HIỂU câu hỏi (người ta đang nói về cuộc nào),
  không bao giờ dùng để dựng nội dung câu trả lời.
- Chỉ dùng asker_token do hệ thống gắn bên dưới. Không nhắc lại, không in, không
  giải thích token này cho người dùng.
- HAI loại kết quả tool, ĐỪNG lẫn:
  * khối GỬI NGUYÊN VĂN = tin đã soạn xong. Chép y hệt, không sửa gì.
  * khối DỮ LIỆU HỌP = nguyên liệu. ĐỌC rồi TRẢ LỜI ĐÚNG CÂU người dùng vừa hỏi,
    bằng lời của bạn, ngắn gọn. TUYỆT ĐỐI không dán nguyên khối đó ra chat.
    Người ta hỏi một câu, không xin một bản ghi. Số liệu, tên riêng, ngày giờ và
    link phải giữ nguyên; không có dữ liệu thì nói thẳng là không có.
- Người dùng hỏi SO SÁNH, NHẬN XÉT hay PHÂN TÍCH ("bản nào tốt hơn", "phân tích
  giúp tôi") thì vẫn phải trả lời đúng câu đó sau khi đọc dữ liệu — không được
  đáp lại bằng cách liệt kê hay dán lại nội dung cuộc họp.
- Phần NỘI BỘ tuyệt đối không được hiện ra chat.
- Không gọi BFL hay bất kỳ tool tạo ảnh/video, terminal, web hoặc Feishu Drive/Doc.
- Nếu không gọi được tool meetings, không suy đoán từ trí nhớ; nói thẳng là chưa
  truy xuất được dữ liệu.

[VĂN PHONG]
Nói chuyện như một đồng nghiệp làm thư ký, không như một cái máy tra cứu.
- Xưng "mình", gọi người dùng là "bạn". Tiếng Việt tự nhiên, đủ ý, không rườm rà.
- Đừng dùng đi dùng lại một câu khuôn mẫu. Cùng một tình huống, hai lần khác
  nhau thì diễn đạt khác nhau — miễn là số liệu và sự thật không đổi.
- Người ta chào, cảm ơn, nói đùa một câu thì đáp lại bình thường rồi mới vào
  việc. Không cần lôi phạm vi công việc ra mỗi lần.
- Không tìm thấy dữ liệu thì nói thẳng, ngắn, và gợi ý bước tiếp theo cụ thể —
  đừng xin lỗi dài dòng và cũng đừng cụt lủn tới mức khô khan.
- Ngoài phạm vi thì từ chối trong một câu, tử tế, rồi nói bạn giúp được gì.
  Không giảng giải, không lặp lại nội quy.
- ĐỪNG NHẠI LẠI NGÔN NGỮ MÁY. Nhãn viết hoa trong kết quả tool ("CHƯA CÓ BIÊN
  BẢN", "đang phiên âm", "held", "waiting_auth", "priority") là mốc để BẠN quét,
  không phải từ để đưa vào câu văn. Dịch sang lời người: "nội dung cuộc này mình
  chưa có" chứ không phải "hệ thống hiện vẫn báo CHƯA CÓ BIÊN BẢN".
- Đừng thuật lại việc mình vừa làm ("mình đã gọi tool", "hệ thống trả về"). Người
  ta cần câu trả lời, không cần biên bản thao tác của bạn.
- Đừng rào trước đón sau. Một câu nói rõ mình biết gì và không biết gì là đủ; ba
  câu xin lỗi vì chưa chắc chắn thì thành nhạt.

[NỘI DUNG CHƯA PHIÊN ÂM XONG]
Cuộc họp mới xong thường CHƯA có bản nguyên văn của hệ thống, nhưng Lark đã có
sẵn bản chép của nó và tool sẽ trả bản đó cho bạn. Khi nhận được nội dung ghi
nguồn là "bản chép sẵn của Lark":
- TRẢ LỜI ĐÚNG CÂU người dùng hỏi bằng nội dung đó. Đừng nói "chưa có biên bản"
  — họ đang cầm nội dung cuộc họp trong tay rồi, nói vậy là sai sự thật.
- Rồi hỏi thêm ĐÚNG MỘT CÂU ở cuối: có cần bản nguyên văn chuẩn không, kèm con
  số ước tính mà tool đưa. Hỏi gọn, đừng giải thích cơ chế bên trong.
- Họ đáp "có"/"ok"/"cần" thì gọi ngay send_transcript_file với đúng
  minute_token của cuộc đang nói. Đừng bắt họ gõ lại tên cuộc họp.
KỶ LUẬT SỐ LIỆU không đổi: những con số, tên riêng, ngày giờ, link trong câu
trả lời phải đúng nguyên như tool trả về. Được viết mềm hơn, KHÔNG được đoán.

[DUYỆT BIÊN BẢN — CHỈ CHỦ TRÌ]
Chủ trì nhận thẻ "Cần bạn duyệt" trước khi người dự được báo.
- Họ nhắn "duyệt <tên>" / "ok phát đi" / "đúng rồi" về cuộc đó -> gọi
  confirm_meeting với đúng cuộc họp đó.
- Họ nhắn "sửa <tên>: ..." hoặc chỉ ra chỗ sai -> gọi edit_meeting, truyền
  NGUYÊN VĂN yêu cầu sửa vào `instruction`. Chép bản mới cho họ xem, rồi nhắc
  họ duyệt. KHÔNG tự gọi confirm_meeting thay họ.
- Tool từ chối (không phải chủ trì) thì nói lại đúng lý do, không thử cách khác."""

_SMALLTALK = {
    "hi", "hello", "hey", "xin chao", "chao", "chao ban", "chao buoi sang",
    "cam on", "cam on ban", "cam on nhe", "thanks", "thank you", "ty",
    "ok", "oke", "okay", "ok ban", "ok nhe",
    "duoc", "duoc roi", "uh", "u", "roi", "tot", "tot roi", "hay qua",
    "vang", "da", "hieu roi", "chao nhe", "bye", "tam biet",
    # "alo" là tin nhắn THẬT trong log ngày 05/08/2026, gõ hai lần, và cả hai
    # lần bot đáp lại bằng câu chặn 153 ký tự về "chưa truy xuất dữ liệu".
    # Người ta gõ "alo" để xem bot còn sống không, không phải để hỏi dữ liệu.
    "alo", "alo ban", "test", "test thu", "a", "?",
}
# Câu hỏi VỀ CHÍNH BOT. Trước 06/08/2026 không có danh sách này, nên "bạn tên
# gì" rơi vào nhánh mặc định "phải gọi tool meetings" — mà không tool nào trả
# lời được câu đó, nên `_on_transform_llm_output` chặn và người dùng nhận về
# câu "mình chưa truy xuất dữ liệu cuộc họp trong lượt này". Hỏi bot về chính
# nó thì bị xử như một câu hỏi dữ liệu hỏng.
#
# Trả lời những câu này KHÔNG cần dữ liệu: hồ sơ đã nằm sẵn trong channel_prompt
# (`v2/profile.py`, gate bơm sang). Miễn trừ ở đây không mở cửa dữ liệu nào —
# `qa._may_see` vẫn là cửa duy nhất, và nó không đổi.
#
# Dựng bằng CHỦ NGỮ × ĐUÔI CÂU thay vì liệt kê tay, sau khi đọc 86 tin nhắn
# thật trong `gateway.log`. Người dùng thật gõ "ok MÀY là ai và luồng xử lý
# của mày là gì" — danh sách viết tay chỉ có "ban" nên câu đó bị chặn bằng câu
# báo lỗi dữ liệu. Xưng hô là thứ mỗi người một kiểu; đuôi câu thì không.
#
# Yêu cầu CHỦ NGỮ ĐỨNG LIỀN TRƯỚC cũng chính là thứ giữ an toàn. Không có nó:
#   "du an HOAT DONG RA SAO"      -> lọt, mà đó là câu hỏi nội dung
#   "cuoc hop nay CHAY THE NAO"   -> lọt
# Có nó thì hai câu trên không khớp, còn "may hoat dong ra sao" thì khớp.
#
# Ba chuỗi đã bị loại sau khi thử ngược lại — đừng thêm lại:
#   "ten ban"       -> khớp "cho minh TEN BAN ghi cuoc hop" ("bản ghi")
#   "ban la gi"     -> khớp "cuoc hop nay BAN LA GI" ("bàn là gì")
#   "dung the nao"  -> khớp "cai do DUNG THE NAO" (hỏi nội dung trong họp)
# "em" chứ KHÔNG phải "e": phép so là substring, nên "e la ai" khớp luôn vào
# giữa chữ "th[e la ai]"… — một chủ ngữ một chữ cái là một cái bẫy.
_SELF_SUBJECTS = ("ban", "may", "bot", "cau", "em")
_SELF_TAILS = (
    "la ai", "ten gi", "ten la gi", "la bot gi", "la con gi", "la ai vay",
    "lam duoc gi", "lam duoc nhung gi", "lam gi duoc", "lam nhung gi",
    "giup duoc gi", "giup gi duoc", "co the lam gi", "co the giup gi",
    "hoat dong the nao", "hoat dong ra sao", "lam viec the nao",
    "chay the nao", "co nho", "nho gi", "co nho khong",
)
_SELF_PHRASES = tuple(
    f"{s} {t}" for s in _SELF_SUBJECTS for t in _SELF_TAILS
) + (
    # Không cần chủ ngữ: tự nó đã chỉ về bot/hệ thống, và đã thử ngược.
    "gioi thieu ban", "gioi thieu ve ban", "tu gioi thieu", "ai tao ra ban",
    "luong xu ly", "luong hoat dong", "he thong hoat dong the nao",
    "cach ban hoat dong", "ban la ai",
)
_HELP_PHRASES = (
    "tro giup", "help", "huong dan su dung", "cach su dung",
) + _SELF_PHRASES
_ACTION_HINTS = (
    "liet ke", "xem ", "tim ", "gui ", "tao ", "duyet", "loai ",
    "tu choi", "lay ", "doc ", "chi tiet", "nguyen van", "hom nay",
    "hom qua", "tuan nay", "ngay qua",
)
_BUSINESS_HINTS = (
    "cuoc hop", "bien ban", "transcript", "minute", "task", "file",
    "tu dien", "glossary",
)
# Người dùng GỌI TÊN bản ghi nguyên văn bằng những chữ này. Cổng write-tool của
# `send_transcript_file` dùng đúng danh sách này (xem `_on_pre_tool_call`).
#
# Vì sao phải nới (ca thật 05/08/2026 13:37): mẫu cũ đòi có "file"/"word"/"docx"/
# "tai ve", hoặc "gui" đi kèm "bien ban"/"transcript"/"nguyen van". Người dùng gõ
# "cho tôi script của cuộc họp test lại luồng" — không chữ nào khớp, tool bị chặn,
# và agent báo ra chat "hệ thống chưa gửi được file script". Nhìn y như hệ thống
# hỏng trong khi thực ra chính cổng bảo vệ chặn.
#
# Ý đồ của cổng KHÔNG đổi: chặn agent tự tiện gửi file khi người dùng không hề
# nhắc tới bản ghi. Điều kiện giờ là "người dùng có gọi tên thứ mình muốn" — vẫn
# đủ chặt, vì file chỉ gửi cho CHÍNH người hỏi và vẫn qua `qa._may_see`.
_TRANSCRIPT_NOUNS = (
    "script", "transcript", "nguyen van", "bien ban", "ban ghi",
    "file", "word", "docx", "tai ve",
)
_MEETING_TOOLS = {
    "list_meetings", "get_meeting", "search_meetings", "get_transcript",
    "send_transcript_file", "create_task", "glossary_pending",
    "glossary_approve", "glossary_reject",
}
_DIRECT_CONFIRM = {
    "list_meetings": "Danh sách cuộc họp của bạn ở ngay phía trên nhé.",
}
_HOUSEKEEPING_TOOLS = {"tool_describe"}
# Câu thay thế khi agent trả lời mà KHÔNG gọi tool nào. Bản cũ đọc như một báo
# lỗi hệ thống ("đã chặn câu trả lời suy đoán") — người dùng không làm gì sai mà
# nhận về một câu nghe như bị từ chối. Ý nghĩa kỹ thuật giữ nguyên (fail-closed,
# vẫn thay câu trả lời), chỉ đổi cách nói và thêm một bước tiếp theo cụ thể.
_NO_TOOL_REPLY = (
    "Mình chưa tra được dữ liệu cuộc họp cho câu này nên chưa dám trả lời — "
    "trả lời theo trí nhớ thì rất dễ sai số liệu.\n"
    "Bạn nhắn lại giúp mình, nói rõ cuộc họp hoặc khoảng thời gian nhé; "
    'ví dụ "các cuộc họp của tôi tuần này" hay "cuộc họp Workforce hôm qua '
    'chốt gì".'
)

# Hồ sơ DỰ PHÒNG. Bản thật do `v2/profile.py` cấp qua gate mỗi lượt — đó mới là
# nguồn sự thật, và nó nằm trong repo. Chuỗi này chỉ chạy khi V2 đời cũ không
# trả trường `profile` (plugin mới + V2 chưa cập nhật). Cố ý ngắn: một hồ sơ
# dài viết cứng ở đây sẽ âm thầm lệch với bản thật rồi không ai biết bản nào
# đang có hiệu lực.
_PROFILE_FALLBACK = (
    "[HỒ SƠ CỦA BẠN]\n"
    "Bạn là trợ lý biên bản họp trên Lark. Bạn trả lời về các cuộc họp người "
    "dùng đã dự, gửi bản nguyên văn dạng file Word, và tạo việc cần làm trong "
    "Lark Task từ một cuộc họp.\n"
    "Người dùng hỏi về bạn thì trả lời thẳng, không cần gọi tool."
)

_SEND_MARK = "===== GỬI NGUYÊN VĂN CHO NGƯỜI DÙNG"
_END_MARK = "===== HẾT PHẦN GỬI"
_INTERNAL_MARK = "===== NỘI BỘ"
# Kênh DỮ LIỆU (qa.CONTEXT_MARK). Khác GỬI NGUYÊN VĂN ở đúng một điều, nhưng là
# điều quan trọng nhất: khối này KHÔNG phải câu trả lời, nên plugin không được
# lấy nó thay cho câu trả lời của agent. Xem `_on_transform_llm_output`.
_CONTEXT_MARK = "===== DỮ LIỆU HỌP"

_turn_lock = threading.Lock()
_turns: dict[str, dict] = {}


# Đ/đ PHẢI dịch tay: NFKD chỉ tách DẤU khỏi nguyên âm, còn `đ` (U+0111) là một
# CHỮ CÁI riêng chứ không phải `d` + dấu, nên nó sống sót qua bước khử dấu rồi
# bị `[^a-zA-Z0-9/]` xoá sạch. Hậu quả (tìm ra 06/08/2026 khi thêm nhóm 34e):
#   "được"  -> "uoc"      nên `_SMALLTALK` có "duoc" mà không bao giờ khớp
#   "duyệt" -> "uyet"     nên cổng write-tool của `glossary_approve` chặn CHÍNH
#                         câu "duyệt MCP" mà nó sinh ra để nhận
#   "đọc"   -> "oc"       nên `_ACTION_HINTS` có "doc " mà vô hiệu
#   "động"  -> "ong"      nên "bạn hoạt động thế nào" không khớp hồ sơ
# Mọi hằng số so khớp trong file này đều viết `d` (kiểu "duyet", "duoc"), tức
# chúng luôn giả định phép dịch này — chỉ là trước đây nó không tồn tại.
_D_MAP = str.maketrans({"đ": "d", "Đ": "D"})


def _plain(text: str) -> str:
    """Lowercase + bỏ dấu để nhận diện vài câu xã giao, không dùng cho dữ liệu."""
    folded = unicodedata.normalize("NFKD", (text or "").translate(_D_MAP))
    folded = "".join(c for c in folded if not unicodedata.combining(c))
    return " ".join(re.sub(r"[^a-zA-Z0-9/]+", " ", folded).lower().split())


def _requires_tool(text: str) -> bool:
    normalized = _plain(text)
    if not normalized or normalized.startswith("/"):
        return False
    if normalized in _SMALLTALK:
        return False
    has_action = any(p in normalized for p in _ACTION_HINTS)
    # Câu hỏi khả năng có thể nhắc "cuộc họp" mà vẫn không cần đọc dữ liệu.
    # Nhưng "bot làm được gì VÀ liệt kê..." có động từ nghiệp vụ thì phải gọi.
    if any(p in normalized for p in _HELP_PHRASES) and not has_action:
        return False
    # Không để tiền tố xã giao trở thành bypass: "chào, gửi transcript X" vẫn
    # là yêu cầu dữ liệu. Chỉ miễn khi phần còn lại không nhắc phạm vi nghiệp vụ.
    if (normalized.startswith(("xin chao ", "chao ", "cam on "))
            and not has_action
            and not any(p in normalized for p in _BUSINESS_HINTS)):
        return False
    # Bot này chỉ có một phạm vi nghiệp vụ. Mọi câu có nội dung khác đều phải
    # chạm nguồn dữ liệu hoặc bị fail-closed ở transform_llm_output.
    return True


def _tool_suffix(name: str) -> str:
    value = (name or "").lower()
    if value in _MEETING_TOOLS:
        return value
    # Tên Hermes đăng ký thật: mcp__meetings__list_meetings. Không dùng
    # endswith("_list_meetings"): một tool ngoài phạm vi tên
    # `evil_list_meetings` sẽ lọt allow-list chỉ nhờ hậu tố giống nhau.
    parts = value.split("__")
    if len(parts) >= 2 and parts[-2] == "meetings" and parts[-1] in _MEETING_TOOLS:
        return parts[-1]
    return ""


def _result_text(result) -> str:
    if isinstance(result, str):
        return result
    if isinstance(result, dict):
        content = result.get("content")
        if isinstance(content, list):
            chunks = [str(x.get("text") or "") for x in content
                      if isinstance(x, dict) and x.get("type") == "text"]
            if chunks:
                return "\n".join(chunks)
    return str(result or "")


def _user_block(result: str) -> str:
    """Tách đúng kênh người dùng; thiếu đủ hai mốc thì không tự đoán ranh giới."""
    start = result.find(_SEND_MARK)
    if start < 0:
        return ""
    start = result.find("\n", start)
    # Mốc do code nối SAU user_text là mốc cuối cùng. Dùng find() đầu tiên sẽ
    # cho một transcript có đúng chuỗi marker tự cắt ngắn kênh người dùng.
    end = result.rfind(_END_MARK)
    if start < 0 or end < 0:
        return ""
    return result[start + 1:end].strip()


_SECRET_PATTERNS = (
    (re.compile(r"(?i)(access_key|ticket)=([^&\s]+)"), r"\1=<redacted>"),
    (re.compile(r"\[V2-ASKER:\s*[^\]\s]+\]", re.I), "[V2-ASKER: <redacted>]"),
    (re.compile(r"(?i)(Authorization[:=]\s*Bearer\s+)[^\s]+"), r"\1<redacted>"),
)
_IGNORED_UNHANDLED_EVENTS = {
    "meeting_room.meeting_room.status_changed_v1",
    "vc.meeting.all_meeting_ended_v1",
    "vc.meeting.all_meeting_started_v1",
    "contact.user.updated_v3",
    "contact.user.created_v3",
    "application.application.installed_v6",
    "application.application.created_v6",
    "user_status_change",
}


def _redact(text: str) -> str:
    out = text or ""
    for pattern, replacement in _SECRET_PATTERNS:
        out = pattern.sub(replacement, out)
    return out


class _SafeLogFilter(logging.Filter):
    """Bỏ event đã biết là không dùng và che vé/credential trước mọi handler."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            rendered = record.getMessage()
            if "processor not found" in rendered:
                if any(f"type: {event_type}" in rendered
                       for event_type in _IGNORED_UNHANDLED_EVENTS):
                    return False
            cleaned = _redact(rendered)
            if cleaned != rendered:
                record.msg = cleaned
                record.args = ()
        except Exception:
            pass
        return True


_log_filter = _SafeLogFilter()


def _install_log_filters() -> None:
    # Lark SDK có handler riêng *và* propagate lên root nên phải gắn cả hai.
    # Không hạ level toàn bộ logger: lỗi kết nối thật vẫn phải hiện.
    handlers = list(logging.getLogger().handlers)
    handlers += list(logging.getLogger("Lark").handlers)
    for handler in handlers:
        if not any(isinstance(f, _SafeLogFilter) for f in handler.filters):
            handler.addFilter(_log_filter)


def _on_pre_llm_call(**kwargs):
    if str(kwargs.get("platform") or "").lower() != PLATFORM:
        return None
    session_id = str(kwargs.get("session_id") or "")
    if not session_id:
        return {"context": _POLICY}
    state = {
        "turn_id": str(kwargs.get("turn_id") or ""),
        "required": _requires_tool(str(kwargs.get("user_message") or "")),
        "user_normalized": _plain(str(kwargs.get("user_message") or "")),
        "tool_called": False,
        "user_results": [],
        "direct_tools": [],
        "context_tools": [],
    }
    with _turn_lock:
        _turns[session_id] = state
    # Ý định "xin bản nguyên văn" sống qua nhiều lượt: hệ thống có thể phải hỏi
    # lại khi hai cuộc trùng tên, và câu trả lời ("bản ngày 29/07") không còn
    # chữ nào để cổng write-tool nhận ra. Xem `_asked_recently`.
    _mark_asked(session_id, state["user_normalized"])
    # Lớp dự phòng ở user-context. Chính sách system-role + asker token đã nằm
    # trong event.channel_prompt; đoạn này không chứa bí mật và không được lưu.
    return {"context": _POLICY}


def _on_post_tool_call(**kwargs):
    suffix = _tool_suffix(str(kwargs.get("tool_name") or ""))
    if not suffix:
        return None
    session_id = str(kwargs.get("session_id") or "")
    if not session_id:
        return None
    # Contract Hermes hiện tại luôn phát status=ok|error|blocked|cancelled.
    # Chỉ `ok` mới được tính: blocked/cancelled vẫn phát post hook để telemetry
    # đóng span, nhưng tuyệt đối không chứng minh đã truy xuất dữ liệu.
    if str(kwargs.get("status") or "").lower() != "ok":
        return None
    text = _result_text(kwargs.get("result"))
    visible = _user_block(text)
    is_context = _CONTEXT_MARK in text
    # V2 vừa mời người dùng lấy bản nguyên văn chuẩn -> lượt sau họ đáp "có" là
    # phải gọi được `send_transcript_file`. Đọc dấu ở đây, TRƯỚC phép kiểm
    # "kết quả có đúng giao thức không" bên dưới, vì dấu nằm trong kênh nội bộ
    # và không phụ thuộc kênh nào được dùng.
    if _OFFER_MARK in text:
        _mark_offered(session_id)
    # Chỉ kết quả do code V2 bọc đúng giao thức nhiều kênh mới chứng minh tool đã
    # chạy thành công. Plain text có thể là exception/adaptor error nhưng hook
    # không gắn status=error; tính nó là thành công sẽ mở lại cửa LLM bịa.
    if not visible and not is_context and _INTERNAL_MARK not in text:
        return None
    with _turn_lock:
        state = _turns.get(session_id)
        if not state:
            return None
        state["tool_called"] = True
        if visible:
            state["user_results"].append(visible)
        elif is_context:
            # DỮ LIỆU: đủ để chứng minh đã truy xuất thật (mở khoá
            # `transform_llm_output`), nhưng KHÔNG được dùng thay câu trả lời.
            # Đây chính là chỗ bot từng dán nguyên transcript/record ra chat khi
            # người dùng hỏi một câu so sánh.
            state["context_tools"].append(suffix)
        elif _INTERNAL_MARK in text:
            state["direct_tools"].append(suffix)
    return None


# Ý ĐỊNH xin bản nguyên văn sống bao lâu sau tin nhắn nêu nó ra.
_ASK_TTL_S = 15 * 60
_asked: dict[str, float] = {}
# Phiên mà CHÍNH BOT vừa mời lấy bản nguyên văn (V2 gắn dấu `_OFFER_MARK` vào
# kênh nội bộ của kết quả tool). Tách khỏi `_asked` vì hai thứ khác nhau về
# nguồn gốc và phải mở cửa theo hai luật khác nhau — xem `_on_pre_tool_call`.
_offered: dict[str, float] = {}

# Dấu MỜI do `v2/qa.OFFER_MARK` sinh ra. Chuỗi phải khớp CHÍNH XÁC hai bên; đây
# là một hợp đồng qua biên giới tiến trình (V2 chạy Python 3.12, plugin chạy
# Python 3.11 của Hermes) nên không có cách nào để trình biên dịch bắt lỗi lệch.
# `v2/selftest.py` khoá chuỗi này lại bằng một test đọc thẳng file plugin.
_OFFER_MARK = "[V2-OFFER: transcript]"

# Câu ĐỒNG Ý sau khi bot mời. Chỉ dùng khi `_offered_recently` — tự chúng không
# mở được cửa nào.
#
# Vì sao so khớp bằng TỪ ĐẦU + độ dài chứ không phải `in`: "co" nằm trong
# "co bao nhieu cuoc hop", "duoc" nằm trong "duoc roi cho minh xem lai". Một
# câu đồng ý thật thì ngắn — người ta gõ "có", "ok", "gửi đi", không ai gõ một
# đoạn văn để nói vâng.
_YES_HEADS = {
    "co", "oke", "ok", "okay", "uh", "u", "um", "vang", "da", "duoc", "dc",
    "can", "gui", "yes", "chuan", "dong", "muon",
}
_YES_MAX_WORDS = 4


def _mark_asked(session_id: str, user_text: str) -> None:
    """Ghi nhớ phiên này VỪA có người nói tới bản nguyên văn."""
    if session_id and any(x in user_text for x in _TRANSCRIPT_NOUNS):
        with _turn_lock:
            _asked[session_id] = time.time()


def _mark_offered(session_id: str) -> None:
    """Ghi nhớ BOT vừa mời phiên này lấy bản nguyên văn."""
    if session_id:
        with _turn_lock:
            _offered[session_id] = time.time()


def _offered_recently(session_id: str) -> bool:
    """Bot có vừa mời lấy bản nguyên văn trong 15 phút qua không."""
    if not session_id:
        return False
    with _turn_lock:
        at = _offered.get(session_id)
        if not at:
            return False
        if time.time() - at > _ASK_TTL_S:
            _offered.pop(session_id, None)
            return False
    return True


def _is_yes(user_normalized: str) -> bool:
    """Tin nhắn này có phải một câu ĐỒNG Ý ngắn không.

    Chỉ có nghĩa khi bot vừa mời (`_offered_recently`) — một mình nó không mở
    cửa nào. Ba lớp chặn nhầm, và cả ba đều cần:
      * độ dài: câu đồng ý thật thì ngắn ("có", "ok", "gửi đi");
      * TỪ ĐẦU: "co" nằm giữa "co bao nhieu cuoc hop" nhưng không đứng đầu một
        câu 4 chữ;
      * không nhắc phạm vi nghiệp vụ: "co cuoc hop nao hom nay" là câu HỎI, dù
        nó bắt đầu bằng "co" và chỉ có 5 chữ.
    """
    words = (user_normalized or "").split()
    if not words or len(words) > _YES_MAX_WORDS:
        return False
    if any(x in user_normalized for x in _BUSINESS_HINTS):
        return False
    return words[0] in _YES_HEADS


def _asked_recently(session_id: str) -> bool:
    """Phiên này có người xin bản nguyên văn trong 15 phút qua không.

    Vì sao cần (ca thật 05/08/2026): cổng write-tool chỉ nhìn TIN NHẮN HIỆN TẠI.
    Người dùng nhắn "gửi transcript cuộc họp X", hệ thống thấy hai cuộc trùng tên
    nên hỏi lại, họ trả lời "bản ngày 29/07" — câu này không có chữ nào trong
    `_TRANSCRIPT_NOUNS` nên tool bị chặn, và bot quay ra bảo họ gõ lại cả câu dài.
    Đúng lúc hệ thống vừa hỏi thì nó lại không chấp nhận câu trả lời.

    Ý đồ bảo vệ giữ nguyên: agent vẫn KHÔNG tự gửi file cho người chưa hề nhắc
    tới bản nguyên văn. Chỉ nới đúng trường hợp chính người đó vừa xin trong cùng
    phiên, và hết hạn sau 15 phút để một câu xin cũ không mở cửa mãi mãi.
    """
    if not session_id:
        return False
    with _turn_lock:
        at = _asked.get(session_id)
        if not at:
            return False
        if time.time() - at > _ASK_TTL_S:
            _asked.pop(session_id, None)
            return False
    return True


def _on_pre_tool_call(**kwargs):
    """Allow-list tool và buộc write tool phải do chính user yêu cầu."""
    session_id = str(kwargs.get("session_id") or "")
    tool_name = str(kwargs.get("tool_name") or "").lower()
    with _turn_lock:
        state = dict(_turns.get(session_id) or {})
    if not state:
        return None

    suffix = _tool_suffix(tool_name)
    if not suffix and tool_name not in _HOUSEKEEPING_TOOLS:
        # Contract plugin Hermes là action/message. `decision/reason` nhìn hợp
        # lý nhưng runtime bỏ qua hoàn toàn (hermes_cli.plugins chỉ đọc action).
        return {"action": "block",
                "message": "Bot MeetingxLark chỉ được phép dùng tool meetings."}

    user = str(state.get("user_normalized") or "")
    allowed_write = True
    if suffix == "create_task":
        allowed_write = any(x in user for x in (
            "tao task", "tao viec", "nhac toi", "nhac viec", "giao viec"))
    elif suffix == "send_transcript_file":
        # Ba đường vào, và chỉ ba:
        #   1. chính tin nhắn này gọi tên bản ghi ("gửi transcript cuộc X");
        #   2. họ vừa xin trong 15 phút qua rồi hệ thống hỏi lại (ca 05/08/2026);
        #   3. BOT vừa mời và họ đáp "có" (luồng mới 07/08/2026: bot trả lời
        #      bằng bản chép của Lark rồi hỏi có cần bản chuẩn không).
        # Đường 3 vẫn cần CẢ HAI vế — có lời mời VÀ có câu đồng ý — nên agent
        # không tự mời rồi tự coi là được đồng ý.
        allowed_write = (any(x in user for x in _TRANSCRIPT_NOUNS)
                         or _asked_recently(session_id)
                         or (_offered_recently(session_id) and _is_yes(user)))
    elif suffix == "glossary_approve":
        allowed_write = "duyet" in user or "approve" in user
    elif suffix == "glossary_reject":
        allowed_write = any(x in user for x in ("bo ", "loai", "tu choi", "reject"))
    if not allowed_write:
        logger.error("[v2-gate] CHẶN write tool không do user yêu cầu: %s", suffix)
        return {"action": "block",
                "message": f"User không trực tiếp yêu cầu thao tác {suffix}."}
    return None


# Trần độ dài cho câu xác nhận tự viết. Một câu "danh sách ở trên nhé" dài
# nhất cũng chỉ cỡ này; dài hơn nghĩa là agent đang kể lại nội dung nó không có.
_CONFIRM_MAX_CHARS = 180


def _safe_confirm(text: str) -> bool:
    """Câu tự viết này có được phép thay câu xác nhận cố định không.

    Bối cảnh: `list_meetings` TỰ gửi danh sách vào khung chat rồi chỉ trả về
    kênh nội bộ, nên agent KHÔNG cầm danh sách. Mọi con số hay tên cuộc họp nó
    viết ra ở lượt đó đều là bịa — đúng lỗi đã đo ngày 04/08/2026 ("hệ thống
    tìm thấy 22 cuộc họp gắn với tài khoản của bạn").

    Nên phép thử không phải "câu này hay không" mà là "câu này có KHẲNG ĐỊNH
    điều gì về dữ liệu không":
      - có CHỮ SỐ nào -> không (đếm, ngày tháng, số thứ tự đều bịa được);
      - nhiều dòng hoặc có đầu dòng -> không (đang liệt kê lại);
      - dài quá -> không;
      - rỗng -> không (để câu cố định lo, đừng gửi tin trống).
    Cố ý KHÔNG kiểm bằng danh sách từ cấm: nó luôn thiếu, và thiếu ở đây là
    một con số sai lọt ra chat.
    """
    body = (text or "").strip()
    if not body or len(body) > _CONFIRM_MAX_CHARS:
        return False
    if "\n" in body or any(ch.isdigit() for ch in body):
        return False
    return not body.lstrip().startswith(("•", "-", "*", "1", "#"))


def _on_transform_llm_output(**kwargs):
    if str(kwargs.get("platform") or "").lower() != PLATFORM:
        return None
    session_id = str(kwargs.get("session_id") or "")
    with _turn_lock:
        state = dict(_turns.get(session_id) or {})

    # Khi tool đã dựng sẵn kênh người dùng, code — không phải LLM — quyết định
    # câu cuối. Điều này loại bỏ việc đổi số, đổi thứ tự hoặc lộ lời dặn nội bộ.
    results = state.get("user_results") or []
    if results:
        return _redact(str(results[-1]))

    if state.get("tool_called") and state.get("direct_tools"):
        last = state["direct_tools"][-1]
        if last in _DIRECT_CONFIRM:
            # Câu của agent được đi tiếp nếu nó AN TOÀN (xem `_safe_confirm`).
            # Bản cũ luôn thay bằng đúng một chuỗi cố định, nên người dùng hỏi
            # mười lần thì nhận về mười câu giống hệt nhau — đó là phần "cứng
            # nhắc" nhìn thấy rõ nhất. Cái phải chặn không phải là văn phong mà
            # là CON SỐ do agent tự bịa (nó không cầm danh sách), nên chặn đúng
            # thứ đó và trả lại quyền diễn đạt.
            own = _redact(str(kwargs.get("response_text") or ""))
            if _safe_confirm(own):
                return own
            return _DIRECT_CONFIRM[last]

    if state.get("required") and not state.get("tool_called"):
        logger.error("[v2-gate] CHẶN câu trả lời không gọi tool meetings: session=%s",
                     session_id)
        return _NO_TOOL_REPLY

    response = _redact(str(kwargs.get("response_text") or ""))
    # Fail-closed nếu mô hình cố in nhãn nội bộ. Nếu có đủ khối user thì tách;
    # nếu không đủ mốc thì không phỏng đoán phần nào là an toàn.
    #
    # `_CONTEXT_MARK` cũng phải chặn: agent dán nguyên khối DỮ LIỆU (kèm cả dòng
    # "ĐỌC RỒI TRẢ LỜI BẰNG LỜI CỦA BẠN") ra chat là đúng cái hỏng đang sửa —
    # và nó là dấu hiệu agent đang chép thay vì trả lời.
    if (_INTERNAL_MARK in response or _SEND_MARK in response
            or _CONTEXT_MARK in response):
        visible = _user_block(response)
        return visible or _NO_TOOL_REPLY
    if response != str(kwargs.get("response_text") or ""):
        return response
    return None


def _cleanup_turn(**kwargs) -> None:
    session_id = str(kwargs.get("session_id") or "")
    if session_id:
        with _turn_lock:
            _turns.pop(session_id, None)


def _cleanup_session(**kwargs) -> None:
    """Phiên kết thúc/reset -> xoá cả ý định xin và lời mời của phiên đó.

    Tách khỏi `_cleanup_turn` (07/08/2026) vì hai cái sống khác nhịp:
    `_turns` chết sau MỖI LƯỢT, còn `_asked`/`_offered` phải sống QUA lượt —
    cả hai luồng chúng phục vụ đều là "bot hỏi ở lượt này, người dùng đáp ở
    lượt sau". Gộp chung thì lời mời chết trước khi câu "có" kịp tới, và triệu
    chứng là bot bảo người dùng gõ lại cả tên cuộc họp — đúng lỗi đã sửa một
    lần ở `_asked_recently`.
    """
    session_id = str(kwargs.get("session_id") or "")
    if not session_id:
        return
    with _turn_lock:
        _turns.pop(session_id, None)
        _asked.pop(session_id, None)
        _offered.pop(session_id, None)


def _ask_v2(union_id: str, user_id: str, name: str,
            chat_type: str = "") -> dict:
    """Gọi v2-gate.bat, đọc một dòng JSON ở stdout."""
    try:
        proc = subprocess.run(
            [GATE_BAT, union_id, user_id, name, chat_type],
            capture_output=True, text=True, timeout=TIMEOUT_S,
            encoding="utf-8", errors="replace", shell=False,
        )
    except subprocess.TimeoutExpired:
        return {"decision": "wait", "reason": "v2 gate quá thời gian"}
    except OSError as exc:
        return {"decision": "wait", "reason": f"không chạy được {GATE_BAT}: {exc}"}

    out = (proc.stdout or "").strip().splitlines()
    if proc.returncode != 0 or not out:
        return {"decision": "wait",
                "reason": f"v2 gate rc={proc.returncode} "
                          f"stderr={(proc.stderr or '')[-200:]}"}
    try:
        return json.loads(out[-1])
    except json.JSONDecodeError:
        return {"decision": "wait", "reason": f"JSON xấu: {out[-1][:120]}"}


def _rewrite_card_click(event) -> None:
    """Dịch cú bấm nút trên thẻ thành yêu cầu tiếng Việt bình thường.

    Adapter Feishu của Hermes (plugins/platforms/feishu/adapter.py ~3038) biến
    mỗi cú bấm nút thành một tin nhắn tổng hợp:

        /card button {"v2": "transcript", "token": "obsg..."}

    Nếu để nguyên, Hermes đi tiếp tới nhánh "lệnh lạ" (gateway/run.py ~15480) và
    trả lời `Unknown command /card` — người dùng bấm nút và nhận về tiếng Anh khó
    hiểu. Hook này chạy ở `pre_gateway_dispatch` (~14261), tức TRƯỚC chỗ đó, nên
    sửa `event.text` ở đây là kịp.

    Chỉ nhận đúng hợp đồng của mình (`v2` + `token`); mọi `/card` khác để nguyên
    cho Hermes xử lý theo cách của nó. Chuỗi dựng ra cố ý chứa chữ "transcript"
    để qua được cổng write-tool ở `_on_pre_tool_call`.
    """
    text = (getattr(event, "text", "") or "").strip()
    if not text.startswith("/card "):
        return
    start = text.find("{")
    if start < 0:
        return
    try:
        value = json.loads(text[start:])
    except json.JSONDecodeError:
        return
    if not isinstance(value, dict) or value.get("v2") != "transcript":
        return
    token = str(value.get("token") or "").strip()
    if not token:
        return
    event.text = f"gửi transcript {token}"
    logger.info("[v2-gate] nút thẻ -> yêu cầu transcript %s", token[:16])


def _on_pre_dispatch(**kwargs):
    event = kwargs.get("event")
    source = getattr(event, "source", None)
    if source is None:
        return None

    platform = getattr(source, "platform", None)
    pname = getattr(platform, "value", platform)
    if str(pname).lower() != PLATFORM:
        return None                       # không phải Lark -> không can thiệp

    # Trước mọi thứ khác: cú bấm nút phải thành câu hỏi bình thường, rồi mới đi
    # qua đúng cổng quyền như một tin nhắn gõ tay.
    _rewrite_card_click(event)

    union_id = (getattr(source, "user_id_alt", "") or "").strip()
    user_id = (getattr(source, "user_id", "") or "").strip()
    name = (getattr(source, "user_name", "") or "").strip()
    chat_type = (getattr(source, "chat_type", "") or "").strip().lower()

    # CHỈ chat 1-1. Chặn ngay ở đây, không phiền tới V2: trong phòng nhiều
    # người, bộ lọc của V2 cấp quyền cho NGƯỜI HỎI nhưng câu trả lời thì cả
    # phòng đọc — kể cả người không có trong danh sách người dự. V2 cũng chặn
    # lần nữa (`gate._refuse_group`), đó mới là lớp có test; lớp này chỉ để
    # khỏi tốn một tiến trình con cho tin chắc chắn bị bỏ.
    #
    # `chat_type` rỗng thì ĐỪNG tự suy: để V2 quyết (nó cảnh báo rồi cho đi
    # tiếp). Đoán "rỗng nghĩa là dm" ở đây là dựng một luật thứ hai song song
    # với luật của V2, và hai luật phân quyền lệch nhau thì cái lỏng hơn thắng.
    if chat_type and chat_type != "dm":
        logger.info("[v2-gate] bỏ tin trong %s (%s) — bot chỉ trả lời chat 1-1",
                    chat_type, getattr(source, "chat_id", ""))
        return {"action": "skip", "reason": "v2: chi tra loi chat 1-1"}

    res = _ask_v2(union_id, user_id, name, chat_type)
    decision = res.get("decision")

    if decision == "allow":
        logger.info("[v2-gate] cho vào: %s (%s)", name or user_id,
                    res.get("open_id", ""))
        token = (res.get("asker_token") or "").strip()
        if not token:
            # V2 không cấp được vé (lỗi DB?) -> vẫn cho vào, nhưng nói to: tầng
            # dữ liệu sẽ từ chối và người dùng sẽ thấy bot "không nhận ra tôi".
            logger.warning("[v2-gate] KHÔNG có asker_token cho %s — bot sẽ "
                           "không đọc được biên bản của người này", union_id)
            return {"action": "allow"}
        # Vé nằm trong system prompt TẠM THỜI của đúng event, không còn chèn vào
        # user message. Hermes lưu user message vào session DB; cách cũ vì vậy
        # vừa làm lịch sử nhiễu vừa để vé ngắn hạn nằm trong DB/log. channel_prompt
        # được thêm ở API-call time và không persist vào transcript.
        prior = (getattr(event, "channel_prompt", "") or "").strip()
        identity = (
            f"[V2-ASKER: {token}]\n"
            "Dùng chính chuỗi trên làm asker_token cho mọi tool MCP meetings "
            "trong lượt này; tuyệt đối không hiện nó cho người dùng."
        )
        # HỒ SƠ + KÝ ỨC do V2 cấp (06/08/2026) — xem `v2/profile.py` và
        # `v2/gate._memory_block`. Cả hai đi đường channel_prompt như vé phiên:
        # là system prompt TẠM THỜI của đúng event, không bị Hermes lưu vào
        # session DB. Nhờ vậy ký ức luôn là bản V2 vừa đọc từ SQLite, chứ không
        # phải một bản chụp cũ nằm lại trong lịch sử phiên.
        profile = (res.get("profile") or "").strip() or _PROFILE_FALLBACK
        memory = (res.get("memory") or "").strip()
        event.channel_prompt = "\n\n".join(
            x for x in (prior, _POLICY, profile, memory, identity) if x)
        return {"action": "allow"}

    if decision == "invite":
        logger.info("[v2-gate] đã gửi link cấp quyền cho %s (%s)",
                    name or user_id, union_id)
        return {"action": "skip", "reason": "v2: đã gửi link cấp quyền"}

    logger.info("[v2-gate] chặn %s (%s): %s", name or user_id, union_id,
                res.get("reason", "chưa cấp quyền"))
    return {"action": "skip", "reason": f"v2: {res.get('reason', 'chưa cấp quyền')}"}


def register(ctx) -> None:
    _install_log_filters()
    ctx.register_hook("pre_gateway_dispatch", _on_pre_dispatch)
    ctx.register_hook("pre_llm_call", _on_pre_llm_call)
    ctx.register_hook("pre_tool_call", _on_pre_tool_call)
    ctx.register_hook("post_tool_call", _on_post_tool_call)
    ctx.register_hook("transform_llm_output", _on_transform_llm_output)
    ctx.register_hook("post_llm_call", _cleanup_turn)
    ctx.register_hook("on_session_end", _cleanup_session)
    ctx.register_hook("on_session_reset", _cleanup_session)
    ctx.register_hook("on_session_finalize", _cleanup_session)
