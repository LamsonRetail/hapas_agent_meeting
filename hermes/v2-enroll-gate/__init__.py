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
              SQLite nên sống qua gateway restart và reset tự động. Riêng lệnh
              `/reset` do người dùng chủ động sẽ xoá cả ký ức này để thật sự mở
              một đoạn chat sạch.
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
POLICY_VERSION = "meetingxlark-v2-safety-2026-08-19.1"
_POLICY = f"""[CHÍNH SÁCH HỆ THỐNG {POLICY_VERSION}]
Bạn là bot chuyên dụng cho dữ liệu cuộc họp MeetingxLark V2 trên Feishu.
- Ranh giới nằm ở CÂU TRẢ LỜI, không ở loại câu hỏi: hễ bạn định nói ra một con
  số, một ngày giờ, một cái link, một tên cuộc họp hay bất cứ điều gì về cuộc
  họp của người dùng, thì trong CHÍNH lượt này bạn phải gọi tool `meetings` để
  lấy. Chưa gọi thì đừng nói ra — hệ thống sẽ chặn, và bịa một con số còn tệ
  hơn nhiều so với việc hỏi lại một câu.
- Còn lại thì cứ nói chuyện bình thường: người ta chào, gọi thử, hỏi bạn là ai,
  hỏi bạn làm được gì, nói đùa, hỏi vu vơ — trả lời tự nhiên bằng hồ sơ đã có
  sẵn ở đây. Đừng gọi tool cho có, và đừng bắt người ta phải hỏi đúng khuôn.
- Không dùng kết quả tool hoặc danh sách cuộc họp từ lượt cũ để trả lời lượt
  mới. Ngữ cảnh cũ chỉ dùng để HIỂU câu hỏi (người ta đang nói về cuộc nào),
  không bao giờ dùng để dựng nội dung câu trả lời.
- "Mới nhất" / "gần nhất" là theo THỜI GIAN DIỄN RA của cuộc họp, không phải
  cuộc được nhắc gần đây nhất trong ký ức. Với câu hỏi này BẮT BUỘC gọi
  latest_meeting để backend chọn đúng MỘT cuộc và khóa minute_token; không được
  dùng search_meetings với từ chung chung như "họp"/"meeting", và không được
  lấy thẳng minute_token cũ trong khối ký ức rồi gọi get_meeting/gửi file.
- SỐ THỨ TỰ ("cuộc 3", "gửi bản dịch 2") chỉ danh sách bạn VỪA gửi trong phiên
  này. Đó là dùng ngữ cảnh để HIỂU câu hỏi — hợp lệ. Nhưng nếu danh sách đó
  không còn trong ngữ cảnh, HỎI LẠI tên cuộc họp; đoán số thứ tự là gửi nhầm
  bản ghi cho người ta.
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

[HAI BẢN DỊCH — TRỤC PHÂN LOẠI CHÍNH CỦA HỆ THỐNG NÀY]
Mỗi cuộc họp có thể có hai bản chép khác nhau về CHẤT LƯỢNG. Người dùng cần biết
mình đang đọc bản nào thì mới quyết định được có xin bản tốt hơn hay không, nên
LUÔN gọi đúng tên và LUÔN nói rõ nguồn:
  "bản dịch từ Lark"             — Lark tự nghe, có ngay khi họp xong, nhưng
                                   tên riêng và thuật ngữ dễ sai.
  "bản dịch từ server của Hapas" — hệ thống tự phiên âm lại, CHẤT LƯỢNG HƠN,
                                   và đây là bản gửi ra file Word.

BA TRƯỜNG HỢP, ba cách xử lý — đừng lẫn:
1. Tool trả nội dung nguồn "bản dịch từ Lark": trả lời đúng câu người dùng hỏi,
   rồi LUÔN hỏi thêm đúng một câu xem họ có muốn lấy bản dịch chuẩn từ Hapas
   không. Nếu bản chưa có, nói rõ chỉ khi họ đồng ý hệ thống mới ưu tiên xử lý
   và tự gửi file Word khi xong; dùng ETA nếu tool có đưa.
2. Tool xác nhận bản Hapas "ĐÃ SẴN SÀNG": vẫn hỏi họ có muốn nhận file Word
   ngay không. Nếu Lark không đọc được mà tool trả nội dung Hapas thì dùng nội
   dung ấy, nói rõ nguồn, rồi vẫn hỏi có muốn nhận file không.
3. Tool nói chưa có bản dịch nào: nói thẳng tình trạng bằng lời người (chờ chủ
   bản ghi cấp quyền / đang dịch / hỏng) và bước tiếp theo. Không bịa nội dung,
   không hứa thời điểm mà tool không đưa; nếu đó là một cuộc xác định được thì
   vẫn hỏi họ có muốn hệ thống xử lý bản chuẩn Hapas không.

HỌ CHỈ ĐỊNH NGUỒN THÌ PHẢI ĐỌC ĐÚNG NGUỒN ĐÓ. "phân tích qua bản dịch từ server
Hapas", "theo bản chuẩn", "đọc bản Hapas" -> gọi get_transcript kèm
`source="hapas"`. TUYỆT ĐỐI không đáp "chưa thể phân tích trực tiếp từ bản Hapas"
— nay đã có đường, và câu đó là câu trả lời SAI. Không chỉ định gì thì giữ mặc
định bản Lark như ba trường hợp trên.

MỘT NGUỒN MỘT LƯỢT: đã đọc theo nguồn nào thì đi hết các phần của nguồn ĐÓ, đừng
kéo thêm nguồn kia trong cùng lượt. Hai bản gộp lại cỡ 64.000 ký tự, và câu trả
lời sẽ loãng đi vì thế chứ không sâu hơn. Muốn so sánh hai bản thì nói với người
dùng là sẽ đọc lần lượt, rồi làm ở lượt sau.

Không thêm câu chân trang "Nguồn mặc định: Meeting Note của Lark". Nguồn nội
dung vẫn phải gọi đúng tên khi cần, nhưng câu cuối dành cho lời mời Hapas.

ĐỪNG dùng chữ "biên bản" để nói về việc hệ thống đã xử lý xong hay chưa: trong
hệ thống này "biên bản" là BẢN TÓM TẮT, còn thứ người ta quan tâm là có BẢN DỊCH
nào và của ai. Hai thứ đó lệch nhau, và nói lẫn là làm người đọc hiểu sai.
KỶ LUẬT SỐ LIỆU không đổi: những con số, tên riêng, ngày giờ, link trong câu
trả lời phải đúng nguyên như tool trả về. Được viết mềm hơn, KHÔNG được đoán."""

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
#
# "ban dich"/"hapas" thêm 10/08/2026 cùng lượt đổi TÊN GỌI: chân trang nay mời
# người ta nhắn "gửi bản dịch 2". Đổi tên hiển thị mà quên mở cổng cho chính câu
# lệnh mình vừa mời là kiểu lỗi tự gây ra tệ nhất — bot bảo gõ A rồi chặn A.
# Tên cũ giữ nguyên: ai đã quen "gửi nguyên văn" thì vẫn phải chạy.
_TRANSCRIPT_NOUNS = (
    "script", "transcript", "nguyen van", "bien ban", "ban ghi",
    "ban dich", "hapas", "file", "word", "docx", "tai ve",
)
# Người dùng XIN GỬI — dò cả ĐỘNG TỪ, không chỉ danh từ. Ca thật 19/08/2026
# 08:50:42: "Gửi cho tôi chatbot nhân sự" bị chặn vì câu đó không có danh từ nào
# ở trên, rồi bot bắt gõ lại đúng khuôn "Gửi file Word bản dịch cuộc họp ...".
# Đúng cái mà chú thích ở `_on_pre_tool_call` gọi là đẩy cái dở của backend ra
# thành việc của người dùng, và đúng luật user chốt 10/08: cách nói là vô hạn,
# cổng phải MẶC ĐỊNH CHO QUA chứ không bắt khớp khuôn.
#
# Vì sao nới thế này vẫn an toàn: ý đồ của cổng là chặn AGENT tự tiện gửi khi
# người dùng KHÔNG hề xin. Một động từ xin-gửi trong tin của CHÍNH người dùng là
# bằng chứng trực tiếp họ có xin. File vẫn chỉ tới người hỏi và vẫn qua
# `qa._may_see`, nên nới ở đây không nới quyền đọc của ai.
# CHỈ động từ gửi/tải, KHÔNG lấy đại từ ("cho minh", "cho toi"). Bản đầu có
# chúng và làm câu "cho mình cuộc 2" lọt cổng — phá đúng luật CẢ HAI VẾ (phải có
# lời mời VÀ có câu đáp) ở đường 3/4 bên dưới, tức agent tự mời rồi tự coi là
# được đồng ý. Test `CHƯA mời mà chỉ vào một cuộc -> vẫn chặn` bắt được ngay.
_ASK_TO_SEND = ("gui", "tai ve", "tai xuong", "download")
_MEETING_TOOLS = {
    "list_meetings", "latest_meeting", "get_meeting", "search_meetings",
    "get_transcript", "send_transcript_file", "create_task", "glossary_pending",
    "glossary_approve", "glossary_reject",
}
_HOUSEKEEPING_TOOLS = {"tool_describe"}
# Câu thay thế khi agent khẳng định số liệu mà chưa hề tra (xem `_claims_data`).
# NGẮN lại 10/08/2026: bản cũ dài bốn dòng kèm hai câu ví dụ, và vì cổng cũ bắt
# nhầm cả câu chào nên người dùng gặp nguyên bài giảng đó cho một tiếng "alo".
# Cổng mới hiếm khi chạm tới, nhưng chạm thì cũng chỉ nên là một câu.
_NO_TOOL_REPLY = (
    "Câu này mình phải tra dữ liệu mới dám trả lời, mà lượt vừa rồi mình chưa "
    "tra được — bạn nhắn lại giúp mình, nói rõ cuộc họp hoặc khoảng thời gian "
    "nhé."
)

# Hồ sơ DỰ PHÒNG. Bản thật do `v2/profile.py` cấp qua gate mỗi lượt — đó mới là
# nguồn sự thật, và nó nằm trong repo. Chuỗi này chỉ chạy khi V2 đời cũ không
# trả trường `profile` (plugin mới + V2 chưa cập nhật). Cố ý ngắn: một hồ sơ
# dài viết cứng ở đây sẽ âm thầm lệch với bản thật rồi không ai biết bản nào
# đang có hiệu lực.
_PROFILE_FALLBACK = (
    "[HỒ SƠ CỦA BẠN]\n"
    "Bạn là trợ lý biên bản họp trên Lark. Bạn trả lời về các cuộc họp người "
    "dùng đã dự; sau mỗi cuộc luôn hỏi họ có muốn lấy bản dịch chuẩn từ Hapas "
    "không, và chỉ xử lý/gửi file Word khi họ đồng ý; đồng thời tạo việc "
    "cần làm trong "
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
# pre_gateway_dispatch có định danh Lark; pre_llm_call có sender_id. Giữ ánh
# xạ tối thiểu này để audit của câu cuối mang đúng người mà không nhét PII vào
# command line hay lịch sử session.
_identities: dict[str, dict[str, str]] = {}


# Đ/đ PHẢI dịch tay: NFKD chỉ tách DẤU khỏi nguyên âm, còn `đ` (U+0111) là một
# CHỮ CÁI riêng chứ không phải `d` + dấu, nên nó sống sót qua bước khử dấu rồi
# bị `[^a-zA-Z0-9/]` xoá sạch. Hậu quả (tìm ra 06/08/2026 khi thêm nhóm 34e):
#   "duyệt" -> "uyet"     nên cổng write-tool của `glossary_approve` chặn CHÍNH
#                         câu "duyệt MCP" mà nó sinh ra để nhận
#   "được"  -> "uoc"      nên mọi hằng số viết "duoc" không bao giờ khớp
# Mọi hằng số so khớp còn lại trong file này đều viết `d` (kiểu "duyet",
# "duoc"), tức chúng luôn giả định phép dịch này.
_D_MAP = str.maketrans({"đ": "d", "Đ": "D"})


def _plain(text: str) -> str:
    """Lowercase + bỏ dấu cho các phép so khớp thô, không dùng cho dữ liệu."""
    folded = unicodedata.normalize("NFKD", (text or "").translate(_D_MAP))
    folded = "".join(c for c in folded if not unicodedata.combining(c))
    return " ".join(re.sub(r"[^a-zA-Z0-9/]+", " ", folded).lower().split())


_LATEST_RE = re.compile(
    r"\b(?:moi|gan)\s+(?:day\s+)?nhat\b|\blatest\b|\bmost recent\b")


def _asks_latest(user_normalized: str) -> bool:
    """User hỏi cuộc mới nhất theo giờ họp, không phải cuộc vừa được nhắc."""
    return bool(_LATEST_RE.search(user_normalized or ""))


_LATEST_POLICY = (
    "[YÊU CẦU ĐANG HỎI CUỘC HỌP MỚI NHẤT]\n"
    "Bỏ qua danh sách cuộc được nhắc gần đây trong ký ức. Trước tiên phải gọi "
    "latest_meeting để BACKEND chọn đúng một cuộc theo thời gian diễn ra. Bỏ "
    "keyword nếu người dùng hỏi mới nhất nói chung; chỉ truyền chủ đề mà chính "
    "họ nêu rõ. Không thay bằng search_meetings với từ tự nghĩ ra như 'họp'. "
    "Mọi tool chi tiết hoặc gửi file sau đó phải dùng đúng minute_token mà "
    "latest_meeting đã khóa."
)

_LATEST_MARK_PREFIX = "[V2-LATEST:"
_LATEST_TOKEN_RE = re.compile(r"\[V2-LATEST:\s*([A-Za-z0-9_-]+)\]")


# Cổng "không được trả lời từ trí nhớ" đọc CÂU TRẢ LỜI, không đọc câu hỏi
# (viết lại 10/08/2026, user: "người ta hỏi thiên biến vạn hoá sao mà cứng nhắc").
#
# Bản cũ đoán từ TIN NHẮN VÀO xem câu này có bắt buộc phải gọi tool không, mặc
# định là CÓ, rồi trừ ra bằng ba danh sách chuỗi (`_SMALLTALK` nguyên câu,
# `_SELF_PHRASES` chủ-ngữ×đuôi-câu, `_HELP_PHRASES`). Cách đó sai từ gốc: tập
# câu chào và câu tán gẫu là VÔ HẠN, còn danh sách thì hữu hạn — nên mọi cách
# nói chưa ai nghĩ ra đều rơi vào nhánh "phải gọi tool", và vì không tool nào
# trả lời được câu chào, người dùng nhận về câu chặn. Đã sửa ba lần bằng cách
# thêm chuỗi ("alo", rồi `_SELF_PHRASES`, rồi "alo em") — ba lần đều là vá triệu
# chứng, và lần nào cũng có tin nhắn thật rơi vào đúng cái bẫy đó.
#
# Điều thật sự phải chặn không phải "câu hỏi loại nào" mà là "câu trả lời có
# KHẲNG ĐỊNH điều gì về dữ liệu cuộc họp mà không hề tra không". Vế sau đo được
# chắc chắn: `tool_called` là sự thật do hook `post_tool_call` ghi, còn khẳng
# định thì để lại dấu vết trong CHÍNH câu văn. Nên cổng chuyển sang đo hai thứ
# đó, và mặc định đảo lại thành CHO QUA.
#
# Đổi này KHÔNG nới quyền xem dữ liệu: `qa._may_see` vẫn là cửa duy nhất, và
# tool vẫn phải có vé phiên. Nó chỉ thôi bắt bot im lặng với những câu mà bot
# vốn trả lời được bằng hồ sơ và ký ức đã nằm sẵn trong prompt.
_LINK_RE = re.compile(r"https?://|/minutes/|\bmt[a-z0-9]{6,}\b", re.I)
# NGÀY GIỜ. Một câu trò chuyện không có ngày tháng; một câu kể về cuộc họp thì
# gần như luôn có.
_WHEN_RE = re.compile(r"\d{1,2}\s*[/-]\s*\d{1,2}|\d{1,2}\s*:\s*\d{2}|\b20\d{2}\b")
# SỐ LƯỢNG gắn với danh từ nghiệp vụ — đúng dạng của mọi ca đã đo:
# "Bạn có 8 cuộc họp", "hệ thống tìm thấy 22 cuộc họp gắn với tài khoản của bạn".
#
# Cố ý KHÔNG chặn mọi chữ số, và cũng KHÔNG chặn mọi danh sách (thử lần đầu như
# vậy thì chính câu "mình làm được 3 việc: 1. …" — bot tự giới thiệu, không cần
# dữ liệu gì — bị chặn, tức lại đúng cái cứng nhắc đang sửa). "việc" trần không
# nằm trong danh sách dưới vì lý do đó.
_COUNT_RE = re.compile(
    r"\b\d+\s*(?:cuộc|buổi|biên\s*bản|bản\s*ghi|transcript|minute|task|"
    r"việc\s*cần\s*làm|người|phút|giờ|ngày|tuần)\b", re.I)
# Nhãn máy: agent chỉ có những chữ này nếu nó vừa đọc kết quả tool — hoặc đang
# nhại lại một kết quả tool của LƯỢT CŨ, tức đúng thứ phải chặn.
_MACHINE_LABELS = (
    "chưa có bản tóm tắt", "chưa dựng xong bản tóm tắt", "đã gửi cho bạn",
    "đã có biên bản", "chưa có biên bản",
    "waiting_auth", "minute_token", "held", "delivered",
)
# ĐÃ BỎ (25/08/2026) — `_NO_DATA_MAX_CHARS = 600`, luật "dài hơn 600 ký tự VÀ
# có nhắc cuộc họp/biên bản/file thì coi là khẳng định dữ liệu".
#
# Ca thật 09:57 ngày 25/08: Nguyễn Nam Khánh vừa enroll xong lúc 09:56, hỏi
# "Ngoài ra bạn làm được tất cả những gì" rồi "ý là bạn làm được những công
# việc gì". Đó là câu hỏi VỀ NĂNG LỰC BOT, không cần tra một dòng dữ liệu nào.
# Nhưng đoạn tự giới thiệu năng lực thì đương nhiên vừa dài vừa nhắc "cuộc
# họp/biên bản/file", nên nó tự dính luật này và cả HAI lượt đều bị thay bằng
# câu "mình phải tra dữ liệu mới dám trả lời". Người dùng mới toanh, phút thứ
# hai dùng bot, hỏi "bạn làm được gì" và bị đáp như thế hai lần.
#
# Vì sao bỏ hẳn chứ không nới ngưỡng: bốn luật kia đều là DẤU VẾT CỤ THỂ (ngày
# giờ, số + danh từ nghiệp vụ, link, nhãn máy) — thứ chỉ tool mới cấp được.
# Riêng luật này đo ĐỘ DÀI, tức đoán mò, và mặc định CHẶN khi câu trả lời dài.
# Nó ngược đúng nguyên tắc của chính hàm này (đo dấu vết, mặc định cho qua) —
# cùng loại sai lầm với bản `_claims_data` đầu tiên đoán từ câu hỏi.
#
# Giá phải trả, đã cân nhắc và chấp nhận: lỗ (1) trong docstring dưới rộng ra —
# một bài dài kể chuyện cuộc họp mà KHÔNG có số, ngày, link hay nhãn máy nào
# thì nay lọt. Đổi lại không còn chặn nhầm câu tự giới thiệu. Kỷ luật gọi tool
# vẫn nằm ở `_POLICY` + mô tả tool, và mọi dữ liệu THẬT vẫn phải qua
# `qa._may_see`, nên lỗ này không mở thêm đường xem trộm dữ liệu người khác.


def _claims_data(text: str) -> bool:
    """Câu trả lời này có KHẲNG ĐỊNH điều gì về dữ liệu cuộc họp không.

    Cố ý đo bằng DẤU VẾT CỤ THỂ chứ không bằng chủ đề: một câu nói VỀ biên bản
    ("mình tra được biên bản các cuộc bạn dự") không có gì sai, còn một ngày
    giờ, một con số đếm cuộc họp hay một cái link thì chỉ có thể đến từ dữ liệu
    thật — hoặc từ bịa.

      - NGÀY GIỜ: "06/08/2026", "18:11";
      - SỐ LƯỢNG gắn danh từ nghiệp vụ: "8 cuộc họp", "3 biên bản";
      - LINK hay minute_token: chỉ tool mới cấp được;
      - NHÃN MÁY: đang nhại kết quả tool.

    KHÔNG có luật nào đo độ dài. Xem khối chú thích ngay trên hàm này để biết
    luật đó từng tồn tại, chặn nhầm ca thật nào, và vì sao bỏ.

    Ba lỗ đã biết và CHẤP NHẬN, vì bịt chúng thì chặn nhầm nhiều hơn bắt đúng:
      1. câu bịa không số không ngày ("tuần trước bạn có vài cuộc họp");
      2. danh sách ngắn chỉ có tên cuộc họp, không kèm ngày hay link;
      3. bài DÀI kể chuyện cuộc họp mà không có dấu vết cụ thể nào (lỗ này
         chính là cái giá của việc bỏ luật độ dài, 25/08/2026).
    Chặn (2) phải chặn mọi danh sách, mà "mình làm được 3 việc: 1. …" — bot tự
    giới thiệu, không cần dữ liệu — cũng là một danh sách. Đổi lại: mọi cách
    chào hỏi/tán gẫu chưa ai nghĩ ra đều được trả lời bình thường. Kỷ luật gọi
    tool vẫn nằm ở `_POLICY`, ở mô tả tool, và mọi dữ liệu THẬT vẫn phải qua
    `qa._may_see`.
    """
    body = (text or "").strip()
    if not body:
        return False
    if _WHEN_RE.search(body) or _COUNT_RE.search(body) or _LINK_RE.search(body):
        return True
    low = body.lower()
    if any(x in low for x in _MACHINE_LABELS):
        return True
    # Không dấu vết cụ thể nào -> CHO QUA. Mặc định của hàm này là cho qua, và
    # đừng thêm luật nào đoán bằng độ dài hay chủ đề nữa (xem chú thích trên).
    return False


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
        "user_message": str(kwargs.get("user_message") or ""),
        "user_normalized": _plain(str(kwargs.get("user_message") or "")),
        "tool_called": False,
        "tools_called": [],
        "user_results": [],
        "context_tools": [],
        "latest_requested": _asks_latest(
            _plain(str(kwargs.get("user_message") or ""))),
        "latest_token": "",
    }
    with _turn_lock:
        identity = _identities.get(str(kwargs.get("sender_id") or ""), {})
        state["name"] = identity.get("name", "")
        state["union_id"] = identity.get("union_id", "")
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
    latest_match = (_LATEST_TOKEN_RE.search(text)
                    if suffix == "latest_meeting" else None)
    with _turn_lock:
        state = _turns.get(session_id)
        if not state:
            return None
        state["tool_called"] = True
        state["tools_called"].append(suffix)
        if latest_match:
            state["latest_token"] = latest_match.group(1)
        if visible:
            state["user_results"].append(visible)
        elif is_context:
            # DỮ LIỆU: đủ để chứng minh đã truy xuất thật (mở khoá
            # `transform_llm_output`), nhưng KHÔNG được dùng thay câu trả lời.
            # Đây chính là chỗ bot từng dán nguyên transcript/record ra chat khi
            # người dùng hỏi một câu so sánh.
            state["context_tools"].append(suffix)
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
    # Thêm 10/08/2026 cùng lượt đổi lời mời sang "muốn nhận luôn không": người
    # ta đáp đúng bằng động từ của lời mời — "cho mình luôn", "nhận luôn nhé",
    # "lấy giúp mình". Ba lớp chặn nhầm ở trên vẫn giữ nguyên.
    "cho", "nhan", "lay",
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


# Câu CHỈ VÀO một mục trong danh sách vừa gửi. Chỉ có nghĩa khi
# `_offered_recently` — tự nó không mở cửa nào.
#
# Ngắn + có SỐ là đủ, và cố ý không thêm điều kiện nào nữa: "cho mình cuộc 2",
# "cái 2", "số 3 nhé", "cuộc 04/08" đều là một người đang chỉ vào dòng họ vừa
# đọc. Dài hơn thì không còn là câu chỉ trỏ mà là một yêu cầu khác.
_PICK_MAX_WORDS = 8
_PICK_RE = re.compile(r"\b\d{1,2}\b")


def _picks_item(user_normalized: str) -> bool:
    """Tin nhắn này có chỉ vào một cuộc trong danh sách vừa gửi không."""
    words = (user_normalized or "").split()
    if not words or len(words) > _PICK_MAX_WORDS:
        return False
    return bool(_PICK_RE.search(user_normalized))


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

    # Hai ca thật 14/08/2026:
    #   1. agent lấy token 08-06 thẳng từ chat_memory;
    #   2. sau bản vá search-first, agent tự search từ chung chung "họp", kết quả
    #      loại mất cuộc mới và gửi file `work` 06-08.
    # Lời dặn "chọn dòng đầu" không đủ. `latest_meeting` chọn đúng một token ở
    # backend, còn cổng này buộc mọi read/write tiếp theo dùng CHÍNH token đó.
    args = kwargs.get("args") if isinstance(kwargs.get("args"), dict) else {}
    if state.get("latest_requested"):
        latest_token = str(state.get("latest_token") or "")
        needs_latest = {
            "list_meetings", "search_meetings", "get_meeting",
            "get_transcript", "send_transcript_file", "create_task",
        }
        if suffix in needs_latest and not latest_token:
            # WARNING chứ không ERROR (25/08/2026, áp cho cả ba cú chặn của
            # cổng này): `tools/heartbeat.py:real_errors` đếm mọi dòng có chữ
            # ERROR trong `gateway.log` 15 phút qua và bật WARN cho cả hệ. Một
            # cú chặn ĐÚNG LUẬT là cổng đang làm việc, không phải sự cố — để
            # mức ERROR thì mỗi lần chặn là một lần báo động giả, và người trực
            # quen dần với WARN thì lần hỏng thật sẽ bị bỏ qua. Vẫn giữ WARNING
            # (không phải INFO) để soi được khi cổng nổ hàng loạt.
            logger.warning("[v2-gate] CHẶN %s trước latest_meeting", suffix)
            return {
                "action": "block",
                "message": (
                    "Người dùng đang hỏi cuộc mới nhất theo thời gian. Phải gọi "
                    "latest_meeting trước; không search từ chung chung và không "
                    "dùng token từ ký ức."
                ),
            }
        target_key = ({"get_meeting": "query", "get_transcript": "query",
                       "send_transcript_file": "minute_token",
                       "create_task": "minute_token"}.get(suffix))
        if target_key and latest_token:
            target = str(args.get(target_key) or "").strip()
            if target != latest_token:
                logger.warning("[v2-gate] CHẶN %s sai token cuộc mới nhất",
                               suffix)
                return {
                    "action": "block",
                    "message": (
                        f"Sai cuộc họp: latest_meeting đã khóa minute_token "
                        f"{latest_token}. Hãy gọi {suffix} với đúng token này."
                    ),
                }

    user = str(state.get("user_normalized") or "")
    allowed_write = True
    if suffix == "create_task":
        allowed_write = any(x in user for x in (
            "tao task", "tao viec", "nhac toi", "nhac viec", "giao viec"))
    elif suffix == "send_transcript_file":
        # Bốn đường vào, và chỉ bốn:
        #   1. chính tin nhắn này gọi tên bản ghi ("gửi transcript cuộc X");
        #   2. họ vừa xin trong 15 phút qua rồi hệ thống hỏi lại (ca 05/08/2026);
        #   3. BOT vừa mời và họ đáp "có" (luồng 07/08/2026: bot trả lời bằng
        #      bản dịch từ Lark rồi hỏi có cần bản của server Hapas không);
        #   4. BOT vừa mời và họ CHỈ VÀO một cuộc — "cho mình cuộc 2", "cái 2",
        #      "cuộc 04/08" (thêm 10/08/2026).
        # Đường 3 và 4 đều cần CẢ HAI vế — có lời mời VÀ có câu đáp — nên agent
        # không tự mời rồi tự coi là được đồng ý.
        #
        # Vì sao thêm đường 4: chân trang danh sách nay mời bằng lời ("cần bản
        # dịch chuẩn của cuộc nào thì cứ nói với mình nhé") thay vì dạy cú pháp
        # lệnh. Người ta đáp lại cũng bằng lời, và những câu đó không gọi tên
        # bản ghi — tức BOT MỜI RỒI BOT CHẶN chính câu trả lời cho lời mời của
        # mình. Bắt người dùng gõ đúng khuôn để lọt cổng là đẩy cái dở của
        # backend ra thành việc của họ.
        allowed_write = (any(x in user for x in _TRANSCRIPT_NOUNS)
                         or any(x in user for x in _ASK_TO_SEND)
                         or _asked_recently(session_id)
                         or (_offered_recently(session_id)
                             and (_is_yes(user) or _picks_item(user))))
    elif suffix == "glossary_approve":
        allowed_write = "duyet" in user or "approve" in user
    elif suffix == "glossary_reject":
        allowed_write = any(x in user for x in ("bo ", "loai", "tu choi", "reject"))
    if not allowed_write:
        # INFO, KHÔNG phải ERROR (sửa 19/08/2026): đây là cổng làm ĐÚNG việc của
        # nó, không phải hệ thống hỏng. Ghi ERROR thì `tools/heartbeat.py` đếm
        # vào `gwerr15m` và cả hệ báo WARN mỗi lần cổng chặn đúng — đo được sáng
        # 19/08: hai lần chặn làm heartbeat WARN liên tiếp ba nhịp. Báo động phải
        # có nghĩa, không thì người vận hành học cách bỏ qua nó.
        logger.info("[v2-gate] chặn write tool không do user yêu cầu: %s", suffix)
        return {"action": "block",
                "message": f"User không trực tiếp yêu cầu thao tác {suffix}."}
    return None


def _log_final_response(state: dict, final_text: str) -> None:
    if not final_text or not state:
        return
    try:
        py_exe = os.environ.get("V2_PYTHON", r"C:\Users\PC\AppData\Local\Programs\Python\Python312\python.exe")
        root = os.environ.get("V2_ROOT", os.path.dirname(os.path.abspath(GATE_BAT)))
        payload = json.dumps({
            "name": str(state.get("name") or ""),
            "union_id": str(state.get("union_id") or ""),
            "prompt": str(state.get("user_message") or ""),
            "tool": ",".join(state.get("tools_called") or []) or "hermes_response",
            "response": str(final_text)[:2000],
        }, ensure_ascii=False)
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        proc = subprocess.Popen(
            [py_exe, "-m", "v2", "audit-log", "--stdin-json"],
            cwd=root, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, text=True, encoding="utf-8",
            creationflags=flags,
        )
        if proc.stdin is not None:
            proc.stdin.write(payload)
            proc.stdin.close()
    except Exception:
        pass


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
        res_text = _redact(str(results[-1]))
        _log_final_response(state, res_text)
        return res_text

    response = _redact(str(kwargs.get("response_text") or ""))

    # Không tra gì mà lại khẳng định số liệu -> chặn. Không khẳng định gì thì
    # đây là một câu chuyện trò bình thường, cho qua (xem `_claims_data`).
    if not state.get("tool_called") and _claims_data(response):
        logger.warning("[v2-gate] CHẶN câu khẳng định dữ liệu mà không gọi "
                       "tool: session=%s", session_id)
        _log_final_response(state, _NO_TOOL_REPLY)
        return _NO_TOOL_REPLY

    # Fail-closed nếu mô hình cố in nhãn nội bộ. Nếu có đủ khối user thì tách;
    # nếu không đủ mốc thì không phỏng đoán phần nào là an toàn.
    #
    # `_CONTEXT_MARK` cũng phải chặn: agent dán nguyên khối DỮ LIỆU (kèm cả dòng
    # "ĐỌC RỒI TRẢ LỜI BẰNG LỜI CỦA BẠN") ra chat là đúng cái hỏng đang sửa —
    # và nó là dấu hiệu agent đang chép thay vì trả lời.
    if (_INTERNAL_MARK in response or _SEND_MARK in response
            or _CONTEXT_MARK in response):
        visible = _user_block(response)
        final_text = visible or _NO_TOOL_REPLY
        _log_final_response(state, final_text)
        return final_text
    _log_final_response(state, response)
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


def _forget_v2_memory(union_id: str) -> bool:
    """Xoá ký ức cuộc họp local của đúng người khi họ chủ động `/reset`."""
    union_id = (union_id or "").strip()
    if not union_id:
        return False
    py_exe = os.environ.get(
        "V2_PYTHON", r"C:\Users\PC\AppData\Local\Programs\Python\Python312\python.exe")
    root = os.environ.get("V2_ROOT", os.path.dirname(os.path.abspath(GATE_BAT)))
    try:
        proc = subprocess.run(
            [py_exe, "-m", "v2", "chat-reset", "--union-id", union_id],
            cwd=root, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            timeout=10, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            shell=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        logger.warning("[v2-gate] /reset không xoá được ký ức V2 cho %s",
                       union_id[:12])
        return False
    if proc.returncode != 0:
        logger.warning("[v2-gate] /reset ký ức V2 rc=%s cho %s",
                       proc.returncode, union_id[:12])
        return False
    logger.info("[v2-gate] /reset đã xoá ký ức cuộc họp cho %s", union_id[:12])
    return True


def _on_session_reset(**kwargs) -> None:
    """Reset Hermes -> dọn trạng thái theo phiên cũ và phiên mới."""
    old_session_id = str(kwargs.get("old_session_id") or kwargs.get("session_id") or "")
    new_session_id = str(kwargs.get("new_session_id") or "")
    _cleanup_session(session_id=old_session_id)
    if new_session_id:
        _cleanup_session(session_id=new_session_id)


def _mention_ids(event) -> str:
    """open_id của những người/bot được @ trong tin này, cách nhau bằng dấu phẩy.

    Vì sao cần (06/09/2026): adapter của Hermes coi `@All` là "có gọi bot"
    (`_mentions_self`: `if "@_all" in raw_content: return True`), nên
    `require_mention` không chặn được, và bot đã xen vào một câu `@All` hỏi
    người trong nhóm. V2 quyết định bằng cách so `open_id` với id của chính nó
    — nhưng V2 không thấy payload Lark, nên plugin phải lấy hộ.

    `@All` KHÔNG có open_id nào, nên nó tự nhiên rơi ra khỏi danh sách này.

    Đọc từ `event.raw_message` (payload gốc Lark). Không lấy được thì trả ""
    và V2 fail-closed — im trong nhóm còn hơn nói xen vào chuyện người ta.
    """
    raw = getattr(event, "raw_message", None)
    ms = getattr(raw, "mentions", None)
    if ms is None and isinstance(raw, dict):
        ms = raw.get("mentions")
    out = []
    for m in (ms or []):
        mid = getattr(m, "id", None)
        if mid is None and isinstance(m, dict):
            mid = m.get("id")
        oid = (getattr(mid, "open_id", None)
               or (mid or {}).get("open_id") if isinstance(mid, dict)
               else getattr(mid, "open_id", None))
        oid = (oid or "").strip() if isinstance(oid, str) else ""
        if oid and oid not in out:
            out.append(oid)
    return ",".join(out)


def _ask_v2(union_id: str, user_id: str, name: str,
            chat_type: str = "", chat_id: str = "",
            mentions: str = "") -> dict:
    """Gọi v2-gate.bat, đọc một dòng JSON ở stdout."""
    try:
        proc = subprocess.run(
            [GATE_BAT, union_id, user_id, name, chat_type, chat_id, mentions],
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

    # `/reset` phải thật sự làm sạch cả neo cuộc họp nằm trong SQLite V2. Làm ở
    # pre-dispatch vì hook on_session_reset của Hermes chỉ đưa session_id, không
    # đưa user_id; sau gateway restart sẽ không còn ánh xạ RAM nào để biết cần
    # xoá ký ức của ai. Alias Việt được đổi thành lệnh gốc trước khi Hermes xử lý.
    reset_cmd = (getattr(event, "text", "") or "").strip().lower().split(maxsplit=1)
    reset_cmd = reset_cmd[0] if reset_cmd else ""
    if chat_type == "dm" and reset_cmd in ("/new", "/reset", "/lammoi"):
        _forget_v2_memory(union_id)
        if reset_cmd == "/lammoi":
            return {"action": "rewrite", "text": "/reset"}

    # PHÒNG NHÓM: KHÔNG tự quyết ở đây nữa (28/08/2026) — hỏi V2.
    #
    # Trước đó lớp này bỏ thẳng mọi tin `chat_type != "dm"`, và lý lẽ vẫn đúng
    # nguyên: quyền cấp cho NGƯỜI HỎI còn câu trả lời thì CẢ PHÒNG đọc. Nhưng
    # nay V2 có đường mở theo danh sách trắng (`V2_GROUP_QA_CHATS`), kèm siết
    # phạm vi ở tầng dữ liệu: trong nhóm X chỉ trả lời về cuộc mà nhóm X được
    # mời. Danh sách trắng đó nằm trong `.env` của V2, plugin không đọc được.
    #
    # Nên chặn ở đây là dựng LUẬT THỨ HAI song song với luật của V2 — đúng cái
    # sai mà chú thích cũ ngay dưới đã cảnh báo về `chat_type` rỗng. Một luật,
    # một chỗ: `gate._refuse_group`, lớp duy nhất có test. Đổi lại mỗi tin nhóm
    # tốn một tiến trình con, kể cả tin sẽ bị bỏ — chấp nhận được, vì nhóm
    # không nằm trong danh sách trắng thì Lark cũng đã chặn từ tầng adapter.
    #
    # `chat_id` PHẢI được gửi kèm: V2 dùng nó để biết nhóm có được phép hỏi
    # không, và để buộc phạm vi vào vé phiên. Thiếu nó thì V2 fail-closed.
    if reset_cmd in ("/new", "/reset"):
        return {"action": "allow"}       # lệnh nội bộ, không cần OAuth/V2 gate

    res = _ask_v2(union_id, user_id, name, chat_type,
                  str(getattr(source, "chat_id", "") or ""),
                  _mention_ids(event))
    decision = res.get("decision")

    if decision == "allow":
        logger.info("[v2-gate] cho vào: %s (%s)", name or user_id,
                    res.get("open_id", ""))
        token = (res.get("asker_token") or "").strip()
        with _turn_lock:
            identity = {
                "name": str(res.get("name") or name or ""),
                "union_id": union_id,
            }
            for identity_key in (user_id, union_id):
                if identity_key:
                    _identities[identity_key] = identity
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
        latest_requested = _asks_latest(_plain(
            getattr(event, "text", "") or ""))
        # `chat_memory` sắp theo lúc ĐƯỢC NHẮC, không theo giờ cuộc họp. Với
        # câu "mới nhất", đưa nó cho model chỉ làm tăng khả năng model lấy
        # token cũ trước khi tra. Cổng pre_tool phía trên vẫn là lớp cưỡng chế.
        memory = ("" if latest_requested
                  else (res.get("memory") or "").strip())
        latest_note = _LATEST_POLICY if latest_requested else ""
        event.channel_prompt = "\n\n".join(
            x for x in (prior, _POLICY, profile, latest_note, memory, identity)
            if x)
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
    # Tên hook Hermes gây hiểu nhầm: `on_session_end` hiện được phát ở cuối
    # MỖI `run_conversation`, tức sau từng tin nhắn (turn_finalizer.py), không
    # chỉ ở ranh giới phiên. Chỉ dọn `_turns`; nếu xoá `_offered` ở đây thì bot
    # vừa hỏi "có gửi Hapas không" xong đã quên trước khi user kịp đáp "có".
    ctx.register_hook("on_session_end", _cleanup_turn)
    ctx.register_hook("on_session_reset", _on_session_reset)
    ctx.register_hook("on_session_finalize", _cleanup_session)
