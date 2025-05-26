# backend/app.py
import os
import subprocess
import sys
from flask import Flask, request, jsonify
from flask_cors import CORS
import google.generativeai as genai
from google.generativeai.types import GenerationConfig
from dotenv import load_dotenv
import codecs
import re
import shlex
import ctypes # Check admin win
import traceback # Log err chi tiet
import json
import tempfile
import stat # Cho chmod

# Tai bien .env o goc
load_dotenv(dotenv_path='../.env')

app = Flask(__name__)
# CORS tu frontend (port 5173)
CORS(app, resources={r"/api/*": {"origins": "http://localhost:5173"}})

# --- Cau hinh Gemini ---
GOOGLE_API_KEY = os.getenv('GOOGLE_API_KEY') # Api key mac dinh, hoac tu ui

# --- Anh xa Safety Settings (KHONG DOI) ---
SAFETY_SETTINGS_MAP = {
    "BLOCK_NONE": [
        {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_NONE"},
        {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_NONE"},
        {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_NONE"},
        {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_NONE"},
    ],
    "BLOCK_ONLY_HIGH": [
        {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_ONLY_HIGH"},
        {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_ONLY_HIGH"},
        {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_ONLY_HIGH"},
        {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_ONLY_HIGH"},
    ],
    "BLOCK_MEDIUM_AND_ABOVE": [ # Mac dinh
        {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_MEDIUM_AND_ABOVE"},
        {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_MEDIUM_AND_ABOVE"},
        {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_MEDIUM_AND_ABOVE"},
        {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_MEDIUM_AND_ABOVE"},
    ],
     "BLOCK_LOW_AND_ABOVE": [
        {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_LOW_AND_ABOVE"},
        {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_LOW_AND_ABOVE"},
        {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_LOW_AND_ABOVE"},
        {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_LOW_AND_ABOVE"},
    ],
}

# Clean ten HDH
def get_os_name(platform_str):
    if platform_str == "win32": return "windows"
    if platform_str == "darwin": return "macos"
    return "linux" # Ko win, ko mac -> linux

# Helper anh xa ext sang ten ngon ngu
def get_language_name(file_ext):
    if not file_ext: return "code"
    ext_lower = file_ext.lower()
    if ext_lower == 'py': return 'Python'
    if ext_lower == 'sh': return 'Shell Script (Bash)'
    if ext_lower == 'bat': return 'Batch Script'
    if ext_lower == 'ps1': return 'PowerShell'
    if ext_lower == 'js': return 'JavaScript'
    if ext_lower == 'ts': return 'TypeScript'
    if ext_lower == 'html': return 'HTML'
    if ext_lower == 'css': return 'CSS'
    if ext_lower == 'json': return 'JSON'
    if ext_lower == 'yaml': return 'YAML'
    if ext_lower == 'sql': return 'SQL'
    if ext_lower == 'fortios': return 'FortiOS CLI'
    if ext_lower == 'conf': return 'Config File (FortiOS)'
    # Them ngon ngu khac neu can
    return f'file .{ext_lower}' # Mac dinh

# Ham tao prompt yeu cau Gemini sinh code/lenh
def create_prompt(user_input, backend_os_name, target_os_name, file_type):
    file_extension = ""
    file_type_description = ""
    # Xu ly file_type_input co the la ten file hoac chi extension
    if file_type and '.' in file_type:
        file_extension = file_type.split('.')[-1].lower()
        file_type_description = f"một file có tên `{file_type}`"
    elif file_type:
        file_extension = file_type.lower()
        file_type_description = f"một file loại `.{file_extension}` ({get_language_name(file_extension)})"
    else: # Mac dinh la python
        file_extension = "py"
        file_type_description = f"một script Python (`.{file_extension}`)"

    # Dam bao co file_extension hop le de dung lam tag code block
    code_block_tag = file_extension if file_extension and file_extension.isalnum() else 'code'

    # Dac biet cho FortiGate
    is_fortigate_request = "fortigate" in user_input.lower() or "fortios" in user_input.lower()
    if is_fortigate_request and file_extension in ['txt', 'conf', 'text', 'cli', 'fortios', 'log']: # Các ext phổ biến cho config FortiGate
        code_block_tag = 'fortios'
        file_type_description = f"các lệnh FortiOS CLI (thường lưu dưới dạng `.{file_extension}` hoặc tương tự)"
    
    prompt = f"""
Bạn là một trợ lý AI chuyên tạo mã nguồn hoặc các dòng lệnh để thực thi các tác vụ trên máy tính hoặc thiết bị mạng dựa trên yêu cầu của người dùng.
**Môi trường Backend:** Máy chủ đang chạy {backend_os_name}.
**Mục tiêu Người dùng:** Tạo mã/lệnh phù hợp để lưu vào **{file_type_description}** và chạy trên hệ điều hành **{target_os_name}** hoặc thiết bị chuyên dụng như **FortiGate Firewall**.

**YÊU CẦU TUYỆT ĐỐI:**
1.  **PHẢN HỒI CỦA BẠN *CHỈ* ĐƯỢC PHÉP CHỨA KHỐI MÃ/LỆNH.**
2.  Khối mã/lệnh phải được bao trong dấu ```{code_block_tag} ... ```.
    *   Nếu yêu cầu là FortiGate CLI, hãy sử dụng tag ```fortios ... ```.
    *   Nếu là shell script, sử dụng ```sh ... ``` hoặc ```bash ... ```.
    *   Nếu là Python, sử dụng ```python ... ```.
    *   Nếu là Batch, sử dụng ```bat ... ```.
    *   Nếu là PowerShell, sử dụng ```ps1 ... ``` hoặc ```powershell ... ```.
3.  **TUYỆT ĐỐI KHÔNG** bao gồm bất kỳ văn bản nào khác, không giải thích, không lời chào, không ghi chú, không có gì bên ngoài cặp dấu ```{code_block_tag} ... ```. Toàn bộ phản hồi phải là khối mã/lệnh đó.
4.  Đảm bảo mã/lệnh là **an toàn** và **chỉ thực hiện đúng yêu cầu**.
5.  Nếu là script (Python, Shell, PowerShell, Batch):
    *   Sử dụng `try-except` (hoặc cách xử lý lỗi tương đương) để xử lý lỗi cơ bản nếu có thể.
    *   In thông báo kết quả hoặc lỗi ra `stdout` hoặc `stderr` để người dùng biết chuyện gì đang xảy ra.
    *   Đối với Python, đảm bảo tương thích Python 3.
    *   Đối với Shell, ưu tiên cú pháp tương thích `bash`.
    *   Đối với Batch/PowerShell, đảm bảo cú pháp Windows hợp lệ.
6.  Nếu là các lệnh CLI cho thiết bị (ví dụ: FortiGate):
    *   Cung cấp các lệnh cần thiết để đạt được mục tiêu.
    *   Các lệnh phải chính xác và theo đúng cú pháp của thiết bị.
    *   Ví dụ, để vào mode config trên FortiGate là `config system interface`, sau đó là các lệnh `edit`, `set`, `next`, `end`.
    *   Đối với yêu cầu trích xuất cấu hình FortiGate, hãy bao gồm các lệnh `show full-configuration` cho từng mục cần thiết (ví dụ: `config firewall policy`, `config firewall address`, `config firewall service custom`, v.v.) và kết thúc mỗi mục bằng `end`.
7.  LUÔN LUÔN có cơ chế thông báo kết quả của code (ví dụ: if else) nếu là script, hoặc cung cấp các lệnh show/get để xác nhận nếu là CLI thiết bị.
8.  Chú ý và xem xét xem loại file đó khi chạy có hỗ trợ tiếng việt không, nếu có thì hãy ghi kết quả trả về bằng tiếng việt có dấu, nếu không thì hãy ghi không dấu để tránh rối loạn ký tự trong output.  

**Ví dụ Yêu cầu (FortiGate):** Trích xuất cấu hình policy ID 10 và address object "Internal_LAN". (Mục tiêu: FortiGate, Loại file: .txt)
**Mã trả về (Ví dụ cho FortiGate CLI):**
```fortios
config firewall policy
edit 10
show full-configuration
next
end

config firewall address
edit "Internal_LAN"
show full-configuration
next
end
```

**Ví dụ Yêu cầu:** Tạo thư mục 'temp_folder' trên Desktop (Mục tiêu: Windows, Loại file: .bat)
**Mã trả về (Ví dụ cho .bat):**
```bat
@echo off
setlocal

set "target_dir=%USERPROFILE%\Desktop\temp_folder"

if not exist "%target_dir%" (
    mkdir "%target_dir%"
    if %errorlevel% == 0 (
        echo Da tao thu muc: "%target_dir%"
    ) else (
        echo Loi khi tao thu muc: "%target_dir%" >&2
        exit /b 1
    )
) else (
    echo Thu muc da ton tai: "%target_dir%"
)

endlocal
exit /b 0
```

**(Nhắc lại)** Chỉ cung cấp khối mã/lệnh cuối cùng cho **{file_type_description}** trên **{target_os_name}** hoặc thiết bị chuyên dụng trong cặp dấu ```{code_block_tag} ... ```.

**Yêu cầu của người dùng:** "{user_input}"

**Khối mã/lệnh:**
"""
    return prompt

# Ham tao prompt yeu cau Gemini danh gia code
def create_review_prompt(code_to_review, language): # Nhan language la extension (py, sh, bat, v.v...)
    language_name = get_language_name(language)
    code_block_tag = language if language and language.isalnum() else 'code'

    prompt = f"""
Bạn là một chuyên gia đánh giá code **{language_name}**. Hãy phân tích đoạn mã **{language_name}** sau đây và đưa ra nhận xét về:
1.  **Độ an toàn:** Liệu mã có chứa các lệnh nguy hiểm không? Rủi ro? (Đặc biệt chú ý với script hệ thống như Batch/Shell/FortiOS CLI)
2.  **Tính đúng đắn:** Mã có thực hiện đúng yêu cầu dự kiến không? Có lỗi cú pháp hoặc logic nào không?
3.  **Tính hiệu quả/Tối ưu:** Có cách viết tốt hơn, ngắn gọn hơn hoặc hiệu quả hơn trong **{language_name}** không?
4.  **Khả năng tương thích:** Chạy được trên các OS khác không (nếu có thể áp dụng)?
5.  **Không cần đưa code cải tiến**

Đoạn mã **{language_name}** cần đánh giá:
```{code_block_tag}
{code_to_review}
```

**QUAN TRỌNG:** Chỉ trả về phần văn bản nhận xét/đánh giá bằng Markdown. Bắt đầu trực tiếp bằng nội dung đánh giá. Định dạng các khối mã ví dụ (nếu có) trong Markdown bằng ```{code_block_tag} ... ```. Kết thúc bằng dòng 'Mức độ an toàn: An toàn/Ổn/Nguy hiểm'.
"""
    return prompt

# Ham tao prompt yeu cau Gemini go loi code
def create_debug_prompt(original_prompt, failed_code, stdout, stderr, language): # Nhan language la extension
    language_name = get_language_name(language)
    code_block_tag = language if language and language.isalnum() else 'code'

    prompt = f"""
Bạn là một chuyên gia gỡ lỗi **{language_name}**. Người dùng đã cố gắng chạy một đoạn mã **{language_name}** dựa trên yêu cầu ban đầu của họ, nhưng đã gặp lỗi.

**1. Yêu cầu ban đầu của người dùng:**
{original_prompt}

**2. Đoạn mã {language_name} đã chạy và gây lỗi:**
```{code_block_tag}
{failed_code}
```

**3. Kết quả Output (stdout) khi chạy mã:**
```
{stdout if stdout else "(Không có output)"}
```

**4. Kết quả Lỗi (stderr) khi chạy mã:**
```
{stderr if stderr else "(Không có lỗi stderr)"}
```

**Nhiệm vụ của bạn:**
a.  **Phân tích:** Xác định nguyên nhân chính xác gây ra lỗi dựa trên `stderr`, `stdout` và mã nguồn **{language_name}**.
b.  **Giải thích:** Cung cấp một giải thích rõ ràng, ngắn gọn về lỗi cho người dùng bằng Markdown.
c.  **Đề xuất Hành động / Cài đặt:**
    *   **QUAN TRỌNG (CHỈ CHO PYTHON):** Nếu lỗi là `ModuleNotFoundError` (và ngôn ngữ là Python), hãy xác định tên module và đề xuất lệnh `pip install` trong khối ```bash ... ``` DUY NHẤT.
    *   Nếu lỗi do nguyên nhân khác (thiếu file, quyền, cú pháp sai, lệnh không tồn tại trong Batch/Shell/FortiOS CLI, cấu hình môi trường...) hoặc **ngôn ngữ không phải Python**, hãy đề xuất hành động người dùng cần làm thủ công. **KHÔNG đề xuất `pip install` cho ngôn ngữ không phải Python.**
d.  **Sửa lỗi Code:** Nếu lỗi có thể sửa trực tiếp trong mã **{language_name}**, hãy cung cấp phiên bản mã đã sửa lỗi trong khối ```{code_block_tag} ... ``` CUỐI CÙNG. Nếu không thể sửa lỗi trong code, hãy giải thích tại sao.

**QUAN TRỌNG:**
*   Trả về phần giải thích và đề xuất hành động (bằng Markdown) trước.
*   Nếu có lệnh cài đặt pip (chỉ cho Python), đặt nó trong khối ```bash ... ``` riêng.
*   Sau đó, nếu có thể sửa code, cung cấp khối mã ```{code_block_tag} ... ``` CUỐI CÙNG chứa code đã sửa. Không thêm lời dẫn hay giải thích nào khác sau khối mã này.
*   Nếu không sửa được code, chỉ cần giải thích và (nếu có) đề xuất hành động/cài đặt.

**Phân tích và đề xuất:**
"""
    return prompt

# Ham tao prompt yeu cau Gemini giai thich
def create_explain_prompt(content_to_explain, context, language=None): # Nhan language la extension (optional)
    prompt_header = "Bạn là một trợ lý AI giỏi giải thích các khái niệm kỹ thuật một cách đơn giản, dễ hiểu cho người dùng không chuyên."
    prompt_instruction = "\n\n**Yêu cầu:** Giải thích nội dung sau đây bằng tiếng Việt, sử dụng Markdown, tập trung vào ý nghĩa chính và những điều người dùng cần biết. Giữ cho giải thích ngắn gọn và rõ ràng. Bắt đầu trực tiếp bằng nội dung giải thích, không thêm lời dẫn."
    context_description = ""
    language_name = get_language_name(language) if language else "nội dung"
    code_block_tag = language if language and language.isalnum() else 'code'

    try:
        if isinstance(content_to_explain, str) and content_to_explain.strip().startswith('{') and content_to_explain.strip().endswith('}'):
             parsed_json = json.loads(content_to_explain)
             content_to_explain_formatted = json.dumps(parsed_json, ensure_ascii=False, indent=2)
        else:
             content_to_explain_formatted = str(content_to_explain)
    except json.JSONDecodeError:
         content_to_explain_formatted = str(content_to_explain)

    if context == 'code': # Su dung context 'code' chung
        context_description = f"Đây là một đoạn mã **{language_name}**:\n```{code_block_tag}\n{content_to_explain_formatted}\n```"
        prompt_instruction = f"\n\n**Yêu cầu:** Giải thích đoạn mã **{language_name}** này làm gì, mục đích chính của nó là gì, và tóm tắt các bước thực hiện chính (nếu có). Trả lời bằng tiếng Việt, sử dụng Markdown. Bắt đầu trực tiếp bằng nội dung giải thích."
    elif context == 'execution_result':
        context_description = f"Đây là kết quả sau khi thực thi một đoạn mã:\n```json\n{content_to_explain_formatted}\n```"
        prompt_instruction = "\n\n**Yêu cầu:** Phân tích kết quả thực thi này (stdout, stderr, mã trả về). Cho biết lệnh có vẻ đã thành công hay thất bại và giải thích ngắn gọn tại sao dựa trên kết quả. Lưu ý cả các cảnh báo (warning) nếu có. Trả lời bằng tiếng Việt, sử dụng Markdown. Bắt đầu trực tiếp bằng nội dung giải thích."
    elif context == 'review_text':
        context_description = f"Đây là một bài đánh giá code:\n```markdown\n{content_to_explain_formatted}\n```"
        prompt_instruction = "\n\n**Yêu cầu:** Tóm tắt và giải thích những điểm chính của bài đánh giá code này bằng ngôn ngữ đơn giản hơn. Trả lời bằng tiếng Việt, sử dụng Markdown. Bắt đầu trực tiếp bằng nội dung giải thích."
    elif context == 'debug_result':
        debug_language = language
        if isinstance(content_to_explain, str):
            try:
                parsed_debug = json.loads(content_to_explain)
                debug_language = parsed_debug.get('original_language', language)
            except json.JSONDecodeError: pass
        elif isinstance(content_to_explain, dict):
             debug_language = content_to_explain.get('original_language', language)

        language_name = get_language_name(debug_language) if debug_language else "code"

        context_description = f"Đây là kết quả từ việc gỡ lỗi một đoạn mã {language_name}:\n```json\n{content_to_explain_formatted}\n```"
        prompt_instruction = f"\n\n**Yêu cầu:** Giải thích kết quả gỡ lỗi này, bao gồm nguyên nhân lỗi được xác định, ý nghĩa của đề xuất cài đặt package (nếu có và chỉ cho Python), và mục đích của đoạn code {language_name} đã sửa (nếu có). Trả lời bằng tiếng Việt, sử dụng Markdown. Bắt đầu trực tiếp bằng nội dung giải thích."
    elif context == 'error_message':
        context_description = f"Đây là một thông báo lỗi:\n```\n{content_to_explain_formatted}\n```"
        prompt_instruction = "\n\n**Yêu cầu:** Giải thích thông báo lỗi này có nghĩa là gì, nguyên nhân phổ biến có thể gây ra nó, và gợi ý hướng khắc phục (nếu có thể). Trả lời bằng tiếng Việt, sử dụng Markdown. Bắt đầu trực tiếp bằng nội dung giải thích."
    elif context == 'installation_result':
        context_description = f"Đây là kết quả sau khi cài đặt một package Python:\n```json\n{content_to_explain_formatted}\n```"
        prompt_instruction = "\n\n**Yêu cầu:** Phân tích kết quả cài đặt package này. Cho biết việc cài đặt thành công hay thất bại, và giải thích ngắn gọn output/error từ pip. Trả lời bằng tiếng Việt, sử dụng Markdown. Bắt đầu trực tiếp bằng nội dung giải thích."
    else: # Ngu canh mac dinh
         context_description = f"Nội dung cần giải thích:\n```\n{content_to_explain_formatted}\n```"

    full_prompt = f"{prompt_header}{context_description}{prompt_instruction}"
    return full_prompt

# Ham goi Gemini API
def generate_response_from_gemini(full_prompt, model_config_param, is_for_review_or_debug=False):
    global GOOGLE_API_KEY
    ui_api_key = None

    # Dam bao model_config_param la dict
    if not isinstance(model_config_param, dict):
        app.logger.warning(f"model_config nhan dc ko phai dict, ma la {type(model_config_param)}. Dung dict rong.")
        model_config_internal = {}
    else:
        # Ban sao de ko thay doi dict goc
        model_config_internal = model_config_param.copy()

    try:
        ui_api_key = model_config_internal.pop('api_key', None) # Dung dict da copy
        if ui_api_key and not ui_api_key.strip():
            ui_api_key = None

        effective_api_key = ui_api_key if ui_api_key else GOOGLE_API_KEY

        if not effective_api_key:
            app.logger.error("API Key thieu (ca .env va UI).")
            return "Lỗi cấu hình: Thiếu API Key. Vui lòng đặt GOOGLE_API_KEY trong .env hoặc nhập vào Cài đặt."

        try:
            genai.configure(api_key=effective_api_key)
            if ui_api_key:
                 app.logger.info("Dung API Key tu UI.")
        except Exception as config_e:
             key_source = "giao diện" if ui_api_key else ".env"
             app.logger.error(f"Loi config Gemini voi API Key tu {key_source}: {config_e}")
             error_detail = str(config_e)
             if "API key not valid" in error_detail:
                  return f"Lỗi cấu hình: API key từ {key_source} không hợp lệ. Vui lòng kiểm tra lại."
             else:
                  return f"Lỗi cấu hình: Không thể cấu hình Gemini với API key từ {key_source} ({error_detail})."

        model_name = model_config_internal.get('model_name', 'gemini-1.5-flash')
        if not model_name: model_name = 'gemini-1.5-flash'

        temperature = model_config_internal.get('temperature', 0.7)
        top_p = model_config_internal.get('top_p', 0.95)
        top_k = model_config_internal.get('top_k', 40)
        safety_setting_key = model_config_internal.get('safety_setting', 'BLOCK_MEDIUM_AND_ABOVE')
        safety_settings = SAFETY_SETTINGS_MAP.get(safety_setting_key, SAFETY_SETTINGS_MAP['BLOCK_MEDIUM_AND_ABOVE'])

        generation_config = GenerationConfig(
            temperature=float(temperature),
            top_p=float(top_p),
            top_k=int(top_k)
        )

        app.logger.info(f"Goi model: {model_name} voi config: T={temperature}, P={top_p}, K={top_k}, Safety={safety_setting_key}")
        model = genai.GenerativeModel(model_name=model_name)

        response = model.generate_content(
            full_prompt,
            generation_config=generation_config,
            safety_settings=safety_settings
        )

        if not response.candidates and hasattr(response, 'prompt_feedback') and response.prompt_feedback.block_reason:
            block_reason = response.prompt_feedback.block_reason.name
            safety_ratings_str = str(getattr(response.prompt_feedback, 'safety_ratings', 'Không có'))
            app.logger.warning(f"Phan hoi bi chan: {block_reason}. Ratings: {safety_ratings_str}")
            return f"Lỗi: Phản hồi bị chặn bởi cài đặt an toàn (Lý do: {block_reason}). Hãy thử điều chỉnh Safety Settings hoặc prompt."

        raw_text = response.text.strip()

        if is_for_review_or_debug and raw_text:
             lines = raw_text.splitlines()
             cleaned_lines = []
             prefixes_to_remove = (
                 "đây là đánh giá", "here is the review", "phân tích code",
                 "review:", "analysis:", "đây là phân tích", "here is the analysis",
                 "giải thích và đề xuất:", "phân tích và đề xuất:",
                 "đây là giải thích", "here is the explanation", "giải thích:", "explanation:",
                 "```text" # Khong can [thinking], [processing] nua vi co regex moi o duoi
             )
             first_meaningful_line = False
             for line in lines:
                 stripped_line_lower = line.strip().lower()
                 # Loai bo dong chi co thinking/processing
                 if stripped_line_lower.startswith(("[thinking", "[processing")) and stripped_line_lower.endswith("]"):
                     continue
                 if not first_meaningful_line and any(stripped_line_lower.startswith(p) for p in prefixes_to_remove):
                     continue
                 if line.strip():
                     first_meaningful_line = True
                 if first_meaningful_line:
                     cleaned_lines.append(line)
             final_text = "\n".join(cleaned_lines).strip()
             return final_text

        return raw_text

    except Exception as e:
        error_message = str(e)
        model_name_for_error = model_config_internal.get('model_name', 'unknown_model')
        app.logger.error(f"Loi API Gemini ({model_name_for_error}): {error_message}", exc_info=True) # exc_info=True de log traceback
        if "API key not valid" in error_message:
             key_source = "giao diện" if ui_api_key else ".env"
             return f"Lỗi cấu hình: API key từ {key_source} không hợp lệ. Vui lòng kiểm tra."
        elif "Could not find model" in error_message or "permission denied" in error_message.lower():
             return f"Lỗi cấu hình: Không tìm thấy hoặc không có quyền truy cập model '{model_name_for_error}'."
        elif "invalid" in error_message.lower() and any(p in error_message.lower() for p in ["temperature", "top_p", "top_k", "safety_settings"]):
             return f"Lỗi cấu hình: Giá trị tham số (Temperature/TopP/TopK/Safety) không hợp lệ. ({error_message})"
        elif "Deadline Exceeded" in error_message or "timeout" in error_message.lower():
             return f"Lỗi mạng: Yêu cầu tới Gemini API bị quá thời gian (timeout). Vui lòng thử lại."
        elif "SAFETY" in error_message.upper():
             # Lay chi tiet an toan hon
             details_match = re.search(r"Finish Reason: (\w+)", error_message)
             reason_detail = f" (Lý do: {details_match.group(1)})" if details_match else ""
             safety_ratings_match = re.search(r"Safety Ratings: \[(.+?)]", error_message, re.DOTALL)
             ratings_detail = f", Ratings: {safety_ratings_match.group(1)}" if safety_ratings_match else ""
             return f"Lỗi: Yêu cầu hoặc phản hồi có thể vi phạm chính sách an toàn của Gemini.{reason_detail}{ratings_detail} ({error_message[:100]}...)"
        return f"Lỗi máy chủ khi gọi Gemini: {error_message}"

    finally:
        if ui_api_key and GOOGLE_API_KEY and GOOGLE_API_KEY != ui_api_key:
            try:
                genai.configure(api_key=GOOGLE_API_KEY)
            except Exception as reset_e:
                app.logger.warning(f"Ko the reset API key global ve key .env: {reset_e}")
        elif ui_api_key and not GOOGLE_API_KEY:
             pass # Neu chi co key UI, ko can reset

# Ham trich xuat khoi ma
def extract_code_block(raw_text, requested_extension, user_input_for_context=""):
    primary_tags = [requested_extension]
    if requested_extension == 'py': primary_tags.append('python')
    if requested_extension == 'sh': primary_tags.extend(['bash', 'shell'])
    if requested_extension == 'bat': primary_tags.append('batch')
    if requested_extension == 'ps1': primary_tags.append('powershell')

    is_fortigate_request = "fortigate" in user_input_for_context.lower() or "fortios" in user_input_for_context.lower()
    if is_fortigate_request and requested_extension in ['txt', 'conf', 'cli', 'text', 'log', 'fortios']:
        if 'fortios' not in primary_tags: primary_tags.insert(0, 'fortios') # Uu tien fortios
        if 'cli' not in primary_tags: primary_tags.append('cli')
        if 'text' not in primary_tags: primary_tags.append('text') # Gemini co the dung ```text

    unique_primary_tags = []
    for tag in primary_tags:
        if tag and tag not in unique_primary_tags:
            unique_primary_tags.append(tag)
    
    app.logger.info(f"Trich xuat code voi tags: {unique_primary_tags} cho ext: .{requested_extension}")

    for tag in unique_primary_tags:
        # Regex chap nhan tag va co the co them info sau tag tren cung dong (vd ```python filename.py)
        # Sau do la newline, noi dung code, newline va ``` (tim khoi cuoi cung)
        pattern_strict = r"```" + re.escape(tag) + r"(?:[^\S\n].*?)?\s*\n([\s\S]*?)\n```"
        pattern_flexible = r"```" + re.escape(tag) + r"(?:[^\S\n].*?)?\s*([\s\S]*?)\s*```" # Ko bat buoc newline

        for p_idx, pattern_str in enumerate([pattern_strict, pattern_flexible]):
            try:
                matches = list(re.finditer(pattern_str, raw_text, re.IGNORECASE))
                if matches:
                    app.logger.info(f"Tim thay khoi code voi tag: '{tag}' (pattern {p_idx}).")
                    return matches[-1].group(1).strip() # Lay khoi cuoi
            except re.error as re_err:
                app.logger.error(f"Loi Regex voi pattern '{pattern_str}' cho tag '{tag}': {re_err}")
                continue

    # Neu ko co tag cu the, thu tim khoi ``` chung (khoi cuoi)
    generic_matches = list(re.finditer(r"```(?:([\w\-\./\+]+)[^\S\n]*)?\s*\n([\s\S]*?)\n```", raw_text))
    if not generic_matches:
        generic_matches = list(re.finditer(r"```(?:([\w\-\./\+]+)[^\S\n]*)?\s*([\s\S]*?)\s*```", raw_text))

    if generic_matches:
        last_block_match = generic_matches[-1]
        lang_tag_found = last_block_match.group(1)
        code_content = last_block_match.group(2).strip()
        
        if lang_tag_found:
            lang_tag_found = lang_tag_found.strip().lower()
            app.logger.warning(f"Tim thay khoi code chung voi hint '```{lang_tag_found}```. Dung cho '.{requested_extension}'.")
            if lang_tag_found == 'fortios' and is_fortigate_request: return code_content
            if lang_tag_found == requested_extension or \
               (requested_extension == 'py' and lang_tag_found == 'python') or \
               (requested_extension == 'sh' and lang_tag_found in ['bash', 'shell']) or \
               (requested_extension == 'bat' and lang_tag_found == 'batch') or \
               (requested_extension == 'ps1' and lang_tag_found == 'powershell'):
                return code_content
        else:
            app.logger.warning(f"Tim thay khoi code ```...```. Gia su dung cho '.{requested_extension}'.")
        return code_content

    lines = raw_text.splitlines()
    is_likely_direct_code = (
        len(lines) < 30 and # Tang gioi han len chut
        not any(kw in raw_text.lower() for kw in ["response:", "here's", "this will", "explanation:", "note:", "```"]) and
        not raw_text.startswith("I am a large language model")
    )
    if is_likely_direct_code:
        app.logger.warning(f"Ko tim thay khoi ```. Tra ve raw text vi giong code truc tiep cho '.{requested_extension}'. Raw: '{raw_text[:100]}...'")
        return raw_text.strip()

    app.logger.warning(f"Ko tim thay khoi code cho '.{requested_extension}' hoac khoi chung. Tra ve raw text. Raw: '{raw_text[:100]}...'")
    return raw_text.strip() # Fallback cuoi

# Endpoint sinh code
@app.route('/api/generate', methods=['POST'])
def handle_generate():
    data = request.get_json()
    user_input = data.get('prompt')
    model_config = data.get('model_config', {})
    target_os_input = data.get('target_os', 'auto')
    file_type_input = data.get('file_type', 'py')

    if not user_input:
        return jsonify({"error": "Vui lòng nhập yêu cầu."}), 400

    backend_os_name = get_os_name(sys.platform)
    target_os_name = backend_os_name if target_os_input == 'auto' else target_os_input

    file_extension = file_type_input.split('.')[-1].lower() if '.' in file_type_input else file_type_input.lower()
    if not file_extension or not file_extension.isalnum():
        file_extension = 'py' # Default neu rong/ko hop le

    full_prompt = create_prompt(user_input, backend_os_name, target_os_name, file_type_input)
    # Su dung model_config (dict) thay vi model_config.copy() vi generate_response_from_gemini da tu copy
    raw_response = generate_response_from_gemini(full_prompt, model_config, is_for_review_or_debug=False)


    app.logger.info("-" * 20 + " RAW GEMINI RESPONSE (Generate) " + "-" * 20)
    app.logger.info(raw_response)
    app.logger.info("-" * 60)

    if raw_response and not raw_response.startswith("Lỗi"):
        generated_code = extract_code_block(raw_response, file_extension, user_input_for_context=user_input)

        is_likely_raw_text = (generated_code == raw_response) and not generated_code.strip().startswith("```")

        if not generated_code.strip() or is_likely_raw_text:
             app.logger.error(f"AI ko tra ve khoi ma hop le. Phan hoi tho: {raw_response[:200]}...")
             return jsonify({"error": f"AI không trả về khối mã hợp lệ. Phản hồi nhận được bắt đầu bằng: '{raw_response[:50]}...'"}), 500
        else:
            potentially_dangerous = ["rm ", "del ", "format ", "shutdown ", "reboot ", ":(){:|:&};:", "dd if=/dev/zero", "mkfs"]
            code_lower = generated_code.lower()
            detected_dangerous = [kw for kw in potentially_dangerous if kw in code_lower]
            if detected_dangerous:
                app.logger.warning(f"Canh bao: Ma tao ra chua tu khoa nguy hiem: {detected_dangerous}")
            return jsonify({"code": generated_code, "generated_for_type": file_extension})
    elif raw_response:
        status_code = 400 if ("Lỗi cấu hình" in raw_response or "Lỗi: Phản hồi bị chặn" in raw_response) else 500
        return jsonify({"error": raw_response}), status_code
    else:
        return jsonify({"error": "Không thể tạo mã hoặc có lỗi không xác định xảy ra."}), 500

# Endpoint danh gia code
@app.route('/api/review', methods=['POST'])
def handle_review():
    data = request.get_json()
    code_to_review = data.get('code')
    model_config = data.get('model_config', {})
    file_type = data.get('file_type', 'py')

    if not code_to_review:
        return jsonify({"error": "Không có mã nào để đánh giá."}), 400

    language_extension = file_type.split('.')[-1].lower() if '.' in file_type else file_type.lower()
    if not language_extension: language_extension = 'py'

    full_prompt = create_review_prompt(code_to_review, language_extension)
    review_text = generate_response_from_gemini(full_prompt, model_config, is_for_review_or_debug=True)

    if review_text and not review_text.startswith("Lỗi"):
        return jsonify({"review": review_text})
    elif review_text:
        status_code = 400 if ("Lỗi cấu hình" in review_text or "Lỗi: Phản hồi bị chặn" in review_text) else 500
        return jsonify({"error": review_text}), status_code
    else:
        return jsonify({"error": "Không thể đánh giá mã hoặc có lỗi không xác định xảy ra."}), 500

# Endpoint thuc thi code
@app.route('/api/execute', methods=['POST'])
def handle_execute():
    data = request.get_json()
    code_to_execute = data.get('code')
    run_as_admin = data.get('run_as_admin', False)
    file_type_requested = data.get('file_type', 'py')

    if not code_to_execute:
        return jsonify({"error": "Không có mã nào để thực thi."}), 400

    backend_os = get_os_name(sys.platform)
    admin_warning = None
    temp_file_path = None
    command = []

    if '.' in file_type_requested:
         file_extension = file_type_requested.split('.')[-1].lower()
    else:
         file_extension = file_type_requested.lower()
    if not file_extension or not file_extension.isalnum(): file_extension = 'py'

    # FortiGate CLI commands are not "executed" directly by the backend server's OS
    if file_extension == 'fortios':
        app.logger.info(f"Nhan dc lenh FortiOS CLI (.{file_extension}). Day la noi dung se duoc hien thi/luu, ko thuc thi truc tiep.")
        return jsonify({
            "message": "Đã nhận lệnh FortiOS CLI. Lệnh này không được thực thi trực tiếp bởi backend.",
            "output": code_to_execute, # Tra lai noi dung lenh
            "error": "",
            "return_code": 0, # Gia su thanh cong vi chi la hien thi
            "executed_file_type": file_extension,
            "codeThatFailed": "" # Khong co code that bai theo nghia thuc thi
        })


    app.logger.info(f"--- CANH BAO: Chuan bi thuc thi code file .{file_extension} (Admin/Root: {run_as_admin}) ---")

    try:
        # Luu y newline='' de tranh them dong trong tren Windows
        with tempfile.NamedTemporaryFile(mode='w', suffix=f'.{file_extension}', delete=False, encoding='utf-8', newline='') as temp_file:
            temp_file_path = temp_file.name
            temp_file.write(code_to_execute)
        app.logger.info(f"Da luu code vao file tam: {temp_file_path}")

        if backend_os in ["linux", "macos"] and file_extension in ['sh', 'py']:
            try:
                current_stat = os.stat(temp_file_path).st_mode
                os.chmod(temp_file_path, current_stat | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
                app.logger.info(f"Da cap quyen thuc thi (chmod +x) cho: {temp_file_path}")
            except Exception as chmod_e:
                app.logger.error(f"Ko the cap quyen thuc thi file tam: {chmod_e}")

        interpreter_path = sys.executable # Mac dinh la python
        if file_extension == 'py':
            command = [interpreter_path, temp_file_path]
        elif file_extension == 'bat' and backend_os == 'windows':
            command = ['cmd', '/c', temp_file_path]
        elif file_extension == 'ps1' and backend_os == 'windows':
            command = ['powershell', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', temp_file_path]
        elif file_extension == 'sh' and backend_os in ['linux', 'macos']:
             command = ['bash', temp_file_path]
        elif backend_os == 'windows': # Fallback cho Windows
            command = ['cmd', '/c', temp_file_path]
            app.logger.warning(f"Loai file '.{file_extension}' ko xd ro tren Windows, thu chay bang cmd /c.")
        elif backend_os in ['linux', 'macos']: # Fallback cho Unix-like
             command = ['bash', temp_file_path]
             app.logger.warning(f"Loai file '.{file_extension}' ko xd ro tren {backend_os}, thu chay bang bash.")
        else:
             return jsonify({"error": f"Không hỗ trợ thực thi file .{file_extension} trên HĐH backend: {backend_os}"}), 501

        if run_as_admin:
            if backend_os == "windows":
                try:
                    is_admin = ctypes.windll.shell32.IsUserAnAdmin() != 0
                    if not is_admin:
                        admin_warning = "Yeu cau Admin, nhung backend ko co quyen. Thuc thi quyen thuong."
                        app.logger.warning(f"{admin_warning}")
                except Exception as admin_check_e:
                    admin_warning = f"Ko the check admin ({admin_check_e}). Thuc thi quyen thuong."
                    app.logger.error(f"{admin_warning}")
            elif backend_os in ["linux", "darwin"]: # darwin la macos
                try:
                    subprocess.run(['which', 'sudo'], check=True, capture_output=True, text=True, errors='ignore')
                    app.logger.info("Them 'sudo'. Co the can nhap pass console backend.")
                    command.insert(0, 'sudo')
                except (FileNotFoundError, subprocess.CalledProcessError):
                     admin_warning = "Yeu cau Root, nhung ko tim thay 'sudo'. Thuc thi quyen thuong."
                     app.logger.error(f"{admin_warning}")
                except Exception as sudo_check_e:
                     admin_warning = f"Loi khi check sudo ({sudo_check_e}). Thuc thi quyen thuong."
                     app.logger.error(f"{admin_warning}")
            else:
                admin_warning = f"Yeu cau 'Admin/Root' ko ho tro tren HDH ({backend_os}). Thuc thi quyen thuong."
                app.logger.warning(f"{admin_warning}")

        app.logger.info(f"Chuan bi chay lenh: {' '.join(shlex.quote(str(c)) for c in command)}")
        process_env = os.environ.copy()
        process_env["PYTHONIOENCODING"] = "utf-8" # Dam bao output tieng Viet (neu script in ra)

        result = subprocess.run(
            command, capture_output=True, encoding='utf-8', errors='replace', # errors='replace' de tranh UnicodeDecodeError
            timeout=60, check=False, env=process_env, text=True # text=True dam bao stdout/err la str
        )
        output = result.stdout
        error_output = result.stderr
        return_code = result.returncode

        app.logger.info(f"--- Ket qua thuc thi (Ma tra ve: {return_code}) ---")
        if output: app.logger.info(f"Output:\n{output}")
        if error_output: app.logger.info(f"Loi Output:\n{error_output}")
        app.logger.info(f"----------------------------------------------")

        message = "Thực thi file thành công." if return_code == 0 else "Thực thi file hoàn tất (có thể có lỗi)."
        response_data = {
            "message": message, "output": output, "error": error_output, "return_code": return_code,
            "executed_file_type": file_extension,
            "codeThatFailed": code_to_execute
        }
        if admin_warning:
            response_data["warning"] = admin_warning
        return jsonify(response_data)

    except subprocess.TimeoutExpired:
        app.logger.error("Loi: Thuc thi file qua thoi gian (60s).")
        return jsonify({"error": "Thực thi file vượt quá thời gian cho phép.", "output": "", "error": "Timeout", "return_code": -1, "warning": admin_warning, "codeThatFailed": code_to_execute}), 408
    except FileNotFoundError as fnf_error:
        missing_cmd = str(fnf_error)
        err_msg = f"Lỗi hệ thống: Không tìm thấy lệnh '{missing_cmd}' de chay file .{file_extension}."
        if 'sudo' in missing_cmd and run_as_admin and backend_os != "windows":
             err_msg = "Lỗi hệ thống: Lệnh 'sudo' không được tìm thấy. Không thể chạy với quyền root."
        app.logger.error(f"{err_msg}")
        return jsonify({"error": err_msg, "output": "", "error": f"FileNotFoundError: {missing_cmd}", "return_code": -1, "warning": admin_warning, "codeThatFailed": code_to_execute}), 500
    except Exception as e:
        app.logger.error(f"Loi nghiem trong khi thuc thi file tam: {e}", exc_info=True)
        return jsonify({"error": f"Lỗi hệ thống khi thực thi file: {e}", "output": "", "error": str(e), "return_code": -1, "warning": admin_warning, "codeThatFailed": code_to_execute}), 500
    finally:
        if temp_file_path and os.path.exists(temp_file_path):
            try:
                os.remove(temp_file_path)
                app.logger.info(f"Da xoa file tam: {temp_file_path}")
            except Exception as cleanup_e:
                app.logger.error(f"Ko the xoa file tam {temp_file_path}: {cleanup_e}")

# Endpoint go loi code
@app.route('/api/debug', methods=['POST'])
def handle_debug():
    data = request.get_json()
    original_prompt = data.get('prompt', '(Không có prompt gốc)')
    failed_code = data.get('code')
    stdout = data.get('stdout', '')
    stderr = data.get('stderr', '')
    model_config = data.get('model_config', {})
    file_type = data.get('file_type', 'py')

    if not failed_code:
        return jsonify({"error": "Thiếu mã lỗi để gỡ rối."}), 400

    language_extension = file_type.split('.')[-1].lower() if '.' in file_type else file_type.lower()
    if not language_extension: language_extension = 'py'

    full_prompt = create_debug_prompt(original_prompt, failed_code, stdout, stderr, language_extension)
    raw_response = generate_response_from_gemini(full_prompt, model_config, is_for_review_or_debug=True)

    if raw_response and not raw_response.startswith("Lỗi"):
        explanation_part = raw_response
        corrected_code = None
        suggested_package = None

        if language_extension == 'py': # Chi tim pip install cho Python
            install_match = re.search(r"```bash\s*pip install\s+([\w\-==\.\+\[\]]+)\s*```", explanation_part, re.IGNORECASE) # Cho phep extras [ ], version ==
            if install_match:
                suggested_package = install_match.group(1).strip()
                app.logger.info(f"Debug (Python): Phat hien de xuat package: {suggested_package}")
                explanation_part = explanation_part[:install_match.start()].strip() + explanation_part[install_match.end():].strip()

        last_code_block_match = None
        debug_code_block_tag = language_extension if language_extension.isalnum() else 'code'
        patterns_to_try_tags = [debug_code_block_tag]
        if language_extension == 'py': patterns_to_try_tags.append('python')
        if language_extension == 'sh': patterns_to_try_tags.extend(['bash', 'shell'])
        if language_extension == 'bat': patterns_to_try_tags.append('batch')
        if language_extension == 'ps1': patterns_to_try_tags.append('powershell')
        if language_extension == 'fortios': patterns_to_try_tags.extend(['cli', 'text']) # Cho fortios

        unique_debug_tags = []
        for tag_val in patterns_to_try_tags:
            if tag_val and tag_val not in unique_debug_tags:
                unique_debug_tags.append(tag_val)
        if 'code' not in unique_debug_tags: unique_debug_tags.append('code') # Luon thu tag chung

        for lang_tag_for_pattern in unique_debug_tags:
            pattern_strict = r"```" + re.escape(lang_tag_for_pattern) + r"(?:[^\S\n].*?)?\s*\n([\s\S]*?)\n```"
            pattern_flexible = r"```" + re.escape(lang_tag_for_pattern) + r"(?:[^\S\n].*?)?\s*([\s\S]*?)\s*```"
            if lang_tag_for_pattern == 'code': # Tim ``` ```
                 pattern_strict = r"```\s*\n([\s\S]*?)\n```"
                 pattern_flexible = r"```\s*([\s\S]*?)\s*```"

            for p_idx, pattern_str in enumerate([pattern_strict, pattern_flexible]):
                try:
                    matches = list(re.finditer(pattern_str, explanation_part, re.IGNORECASE | re.MULTILINE))
                    if matches:
                        last_code_block_match = matches[-1]
                        app.logger.info(f"Debug: Tim thay code sua loi voi tag '{lang_tag_for_pattern}' (pattern {p_idx}).")
                        break
                except re.error as re_err_debug:
                    app.logger.error(f"Loi Regex debug code sua voi pattern '{pattern_str}' tag '{lang_tag_for_pattern}': {re_err_debug}")
                    continue
            if last_code_block_match: break

        if last_code_block_match:
            start_index = last_code_block_match.start()
            potential_explanation_before_code = explanation_part[:start_index].strip()
            if potential_explanation_before_code:
                 explanation_part = potential_explanation_before_code
            else:
                 explanation_part = f"(AI chỉ trả về code {get_language_name(language_extension)} đã sửa lỗi, không có giải thích)"
            corrected_code = last_code_block_match.group(1).strip()

        explanation_part = re.sub(r"^(Phân tích và đề xuất:|Giải thích và đề xuất:|Phân tích:|Giải thích:)\s*", "", explanation_part, flags=re.IGNORECASE | re.MULTILINE).strip()

        return jsonify({
            "explanation": explanation_part if explanation_part else "(Không có giải thích)",
            "corrected_code": corrected_code,
            "suggested_package": suggested_package,
            "original_language": language_extension # Tra ve ngon ngu goc
        })
    elif raw_response:
        status_code = 400 if ("Lỗi cấu hình" in raw_response or "Lỗi: Phản hồi bị chặn" in raw_response) else 500
        return jsonify({"error": raw_response}), status_code
    else:
        return jsonify({"error": "Không thể thực hiện gỡ rối hoặc có lỗi không xác định xảy ra."}), 500

# Endpoint cai dat package Python
@app.route('/api/install_package', methods=['POST'])
def handle_install_package():
    data = request.get_json()
    package_name = data.get('package_name')

    if not package_name:
        return jsonify({"error": "Thiếu tên package để cài đặt."}), 400

    if not re.fullmatch(r"^[a-zA-Z0-9\-_==\.\+\[\]]+$", package_name.replace('[','').replace(']','')): # Cho phep version, extras
        app.logger.warning(f"Ten package ko hop le bi tu choi: {package_name}")
        return jsonify({"success": False, "error": f"Tên package không hợp lệ: {package_name}"}), 400

    app.logger.info(f"--- Chuan bi cai dat package: {package_name} ---")
    try:
        pip_command_parts = [sys.executable, '-m', 'pip', 'install'] + shlex.split(package_name)
        command = [part for part in pip_command_parts if part] # Loai bo phan tu rong
    except Exception as parse_err:
        app.logger.error(f"Ko the phan tich ten package: {package_name} - {parse_err}")
        return jsonify({"success": False, "error": f"Tên package không hợp lệ: {package_name}"}), 400

    try:
        process_env = os.environ.copy()
        process_env["PYTHONIOENCODING"] = "utf-8"
        result = subprocess.run(
            command, capture_output=True, encoding='utf-8', errors='replace',
            timeout=120, check=False, env=process_env, text=True
        )
        output = result.stdout
        error_output = result.stderr
        return_code = result.returncode

        app.logger.info(f"--- Ket qua cai dat (Ma tra ve: {return_code}) ---")
        if output: app.logger.info(f"Output:\n{output}")
        if error_output: app.logger.info(f"Loi Output:\n{error_output}")
        app.logger.info(f"----------------------------------------------")

        if return_code == 0:
            message = f"Cài đặt '{package_name}' thành công."
            return jsonify({ "success": True, "message": message, "output": output, "error": error_output })
        else:
            message = f"Cài đặt '{package_name}' thất bại."
            detailed_error = error_output.strip().split('\n')[-1] if error_output.strip() else f"Lệnh Pip thất bại với mã trả về {return_code}."
            return jsonify({ "success": False, "message": message, "output": output, "error": detailed_error }), 500

    except subprocess.TimeoutExpired:
        app.logger.error(f"Loi: Cai dat package '{package_name}' qua thoi gian (120s).")
        return jsonify({"success": False, "error": f"Timeout khi cài đặt '{package_name}'.", "output": "", "error": "Timeout"}), 408
    except FileNotFoundError:
         app.logger.error(f"Loi: Khong tim thay '{sys.executable}' hoac pip.")
         return jsonify({"success": False, "error": "Lỗi hệ thống: Không tìm thấy Python hoặc Pip.", "output": "", "error": "FileNotFoundError"}), 500
    except Exception as e:
        app.logger.error(f"Loi nghiem trong khi cai dat package '{package_name}': {e}", exc_info=True)
        return jsonify({"success": False, "error": f"Lỗi hệ thống khi cài đặt: {e}", "output": "", "error": str(e)}), 500

# Endpoint giai thich noi dung
@app.route('/api/explain', methods=['POST'])
def handle_explain():
    data = request.get_json()
    content_to_explain = data.get('content')
    context = data.get('context', 'unknown')
    model_config = data.get('model_config', {})
    file_type = data.get('file_type') # Extension: py, sh, bat...

    if not content_to_explain:
        return jsonify({"error": "Không có nội dung để giải thích."}), 400

    if isinstance(content_to_explain, dict) or isinstance(content_to_explain, list):
         try: content_to_explain = json.dumps(content_to_explain, ensure_ascii=False, indent=2)
         except Exception: content_to_explain = str(content_to_explain)
    else:
        content_to_explain = str(content_to_explain)

    explain_context = 'code' if context == 'python_code' else context # 'python_code' la context cu
    language_for_prompt = file_type if explain_context == 'code' else None

    full_prompt = create_explain_prompt(content_to_explain, explain_context, language=language_for_prompt)
    explanation_text = generate_response_from_gemini(full_prompt, model_config, is_for_review_or_debug=True)

    if explanation_text and not explanation_text.startswith("Lỗi"):
        return jsonify({"explanation": explanation_text})
    elif explanation_text:
        status_code = 400 if ("Lỗi cấu hình" in explanation_text or "Lỗi: Phản hồi bị chặn" in explanation_text) else 500
        return jsonify({"error": explanation_text}), status_code
    else:
        return jsonify({"error": "Không thể tạo giải thích hoặc có lỗi không xác định xảy ra."}), 500

if __name__ == '__main__':
    # Cau hinh logging cua Flask
    if not app.debug: # Chi cau hinh khi ko phai debug mode (vi debug mode co the config rieng)
        import logging
        from logging.handlers import RotatingFileHandler
        # Log vao file
        if not os.path.exists('logs'):
            os.mkdir('logs')
        file_handler = RotatingFileHandler('logs/gemini_executor.log', maxBytes=10240, backupCount=10)
        file_handler.setFormatter(logging.Formatter(
            '%(asctime)s %(levelname)s: %(message)s [in %(pathname)s:%(lineno)d]'
        ))
        file_handler.setLevel(logging.INFO) # Co the set DEBUG de xem chi tiet hon
        app.logger.addHandler(file_handler)
        app.logger.setLevel(logging.INFO)

    app.logger.info('Backend Gemini UI Executor dang khoi dong...')
    print("Backend đang chạy tại http://localhost:5001") # Van giu print nay cho console
    if sys.platform == "win32":
        try:
            is_admin = ctypes.windll.shell32.IsUserAnAdmin() != 0
            if is_admin:
                app.logger.info("Backend dang chay voi quyen Administrator.")
                print("[INFO] Backend đang chạy với quyền Administrator.")
            else:
                app.logger.info("Backend dang chay voi quyen User thong thuong.")
                print("[INFO] Backend đang chạy với quyền User thông thường.")
        except Exception:
            app.logger.warning("Ko the check quyen admin khi khoi dong.")
            print("[CẢNH BÁO] Không thể kiểm tra quyền admin khi khởi động.")

    app.run(debug=True, port=5001)