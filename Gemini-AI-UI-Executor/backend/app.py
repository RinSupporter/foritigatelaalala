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
from netmiko import ConnectHandler
from netmiko import NetmikoTimeoutException, NetmikoAuthenticationException
from paramiko.ssh_exception import SSHException # Sua import SSHException tu paramiko

# Tai bien .env o goc
load_dotenv(dotenv_path='../.env')

app = Flask(__name__)
# CORS tu frontend (port 5173)
CORS(app, resources={r"/api/*": {"origins": "http://localhost:5173"}})

# --- Cau hinh Gemini ---
GOOGLE_API_KEY = os.getenv('GOOGLE_API_KEY')

# --- Đường dẫn đến thư mục chứa file prompt ---
PROMPT_DATA_DIR = os.path.join(os.path.dirname(__file__), 'prompt_data')


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
    "BLOCK_MEDIUM_AND_ABOVE": [
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
    return "linux"

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
    return f'file .{ext_lower}'

# Hàm đọc nội dung từ file prompt, với fallback
def read_prompt_file(filename, default_content=""):
    filepath = os.path.join(PROMPT_DATA_DIR, filename)
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            return f.read().strip()
    except FileNotFoundError:
        app.logger.warning(f"File prompt '{filepath}' không tìm thấy. Sử dụng nội dung mặc định.")
        # Fallback to default if specific file not found (e.g. default_instructions.txt)
        default_filepath = os.path.join(PROMPT_DATA_DIR, f"default_{filename.split('_', 1)[-1]}")
        try:
            with open(default_filepath, 'r', encoding='utf-8') as f_default:
                return f_default.read().strip()
        except FileNotFoundError:
            app.logger.error(f"Cả file prompt '{filepath}' và file mặc định '{default_filepath}' đều không tìm thấy.")
            return default_content
    except Exception as e:
        app.logger.error(f"Lỗi khi đọc file prompt '{filepath}': {e}")
        return default_content


# Ham tao prompt yeu cau Gemini sinh code/lenh
def create_prompt(user_input, backend_os_name, target_os_name, file_type):
    file_extension = ""
    file_type_description = ""
    if file_type and '.' in file_type:
        file_extension = file_type.split('.')[-1].lower()
        file_type_description = f"một file có tên `{file_type}`"
    elif file_type:
        file_extension = file_type.lower()
        file_type_description = f"một file loại `.{file_extension}` ({get_language_name(file_extension)})"
    else:
        file_extension = "py" # Mặc định là python nếu không có gì
        file_type_description = f"một script Python (`.{file_extension}`)"

    code_block_tag = file_extension if file_extension and file_extension.isalnum() else 'code'

    is_target_fortios = target_os_name.lower() == 'fortios'
    is_fortigate_request_context = "fortigate" in user_input.lower() or "fortios" in user_input.lower() or is_target_fortios

    # Xác định file hướng dẫn và ví dụ dựa trên file_extension
    # Đặc biệt xử lý cho FortiOS
    lang_key_for_prompt_files = file_extension
    if is_target_fortios and file_extension in ['txt', 'conf', 'cli', 'log', 'fortios']:
        lang_key_for_prompt_files = 'fortios'
        code_block_tag = 'fortios'
        file_type_description = f"các lệnh FortiOS CLI (thường lưu dưới dạng `.{file_extension}` hoặc tương tự, để chạy trên FortiGate)"
    elif is_fortigate_request_context and file_extension in ['txt', 'conf', 'cli', 'fortios', 'log']:
        lang_key_for_prompt_files = 'fortios'
        code_block_tag = 'fortios'
        file_type_description = f"các lệnh FortiOS CLI (thường lưu dưới dạng `.{file_extension}` hoặc tương tự)"
    elif file_extension == 'fortios': # Nếu file type là 'fortios' thì luôn dùng key 'fortios'
        lang_key_for_prompt_files = 'fortios'
        code_block_tag = 'fortios'
        file_type_description = f"các lệnh FortiOS CLI (để chạy trên FortiGate)"
    
    language_specific_instructions = read_prompt_file(f"{lang_key_for_prompt_files}_instructions.txt", read_prompt_file("default_instructions.txt"))
    language_specific_examples = read_prompt_file(f"{lang_key_for_prompt_files}_exp.txt", read_prompt_file("default_exp.txt"))
    
    # Xây dựng phần hướng dẫn cụ thể cho script hoặc CLI
    script_cli_guidance = ""
    if lang_key_for_prompt_files == 'fortios':
        script_cli_guidance = f"""
6.  Nếu là các lệnh CLI cho thiết bị (ví dụ: FortiGate, mục tiêu là `{target_os_name}`):
{language_specific_instructions}"""
    else: # Mặc định là script
        script_cli_guidance = f"""
5.  Nếu là script ({get_language_name(lang_key_for_prompt_files)}):
{language_specific_instructions}"""


    prompt = rf"""
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
{script_cli_guidance}
7.  LUÔN LUÔN có cơ chế thông báo kết quả của code (ví dụ: if else) nếu là script, hoặc cung cấp các lệnh show/get để xác nhận nếu là CLI thiết bị.
8.  Chú ý và xem xét xem loại file đó khi chạy có hỗ trợ tiếng việt không, nếu có thì hãy ghi kết quả trả về bằng tiếng việt có dấu, nếu không thì hãy ghi không dấu để tránh rối loạn ký tự trong output.

**Ví dụ Yêu cầu và Mã trả về (tham khảo):**
{language_specific_examples}

**(Nhắc lại)** Chỉ cung cấp khối mã/lệnh cuối cùng cho **{file_type_description}** trên **{target_os_name}** hoặc thiết bị chuyên dụng trong cặp dấu ```{code_block_tag} ... ```.

**Yêu cầu của người dùng:** "{user_input}"

**Khối mã/lệnh:**
"""
    return prompt

# Ham tao prompt yeu cau Gemini danh gia code
def create_review_prompt(code_to_review, language):
    language_name = get_language_name(language)
    code_block_tag = language if language and language.isalnum() else 'code'
    if language == 'fortios': code_block_tag = 'fortios'

    prompt = rf"""
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
def create_debug_prompt(original_prompt, failed_code, stdout, stderr, language):
    language_name = get_language_name(language)
    code_block_tag = language if language and language.isalnum() else 'code'
    if language == 'fortios': code_block_tag = 'fortios'

    # Xu ly case original_prompt, stdout, stderr co the la None hoac rong
    processed_original_prompt = original_prompt if original_prompt and original_prompt.strip() else "(Không có hoặc prompt rỗng)"
    processed_stdout = stdout if stdout and stdout.strip() else "(Không có output stdout)"
    processed_stderr = stderr if stderr and stderr.strip() else "(Không có output stderr)"

    prompt = rf"""
Bạn là một chuyên gia gỡ lỗi **{language_name}**. Người dùng đã cố gắng chạy một đoạn mã **{language_name}** dựa trên yêu cầu ban đầu của họ, nhưng đã gặp lỗi.

**1. Yêu cầu ban đầu của người dùng (Prompt gốc đã tạo ra mã lỗi):**
```text
{processed_original_prompt}
```

**2. Đoạn mã {language_name} đã chạy và gây lỗi:**
```{code_block_tag}
{failed_code}
```

**3. Kết quả Output (stdout) khi chạy mã:**
```text
{processed_stdout}
```

**4. Kết quả Lỗi (stderr) khi chạy mã:**
```text
{processed_stderr}
```

**Nhiệm vụ của bạn:**
a.  **Phân tích:** Dựa vào **Yêu cầu ban đầu của người dùng**, `stderr`, `stdout` và mã nguồn **{language_name}**, hãy xác định nguyên nhân chính xác gây ra lỗi. Việc hiểu rõ **Yêu cầu ban đầu** là rất quan trọng để biết mục tiêu của người dùng.
b.  **Giải thích:** Cung cấp một giải thích rõ ràng, ngắn gọn về lỗi cho người dùng bằng Markdown. Nếu có thể, hãy liên hệ giải thích với **Yêu cầu ban đầu của người dùng**.
c.  **Đề xuất Hành động / Cài đặt:**
    *   **QUAN TRỌNG (CHỈ CHO PYTHON):** Nếu lỗi là `ModuleNotFoundError` (và ngôn ngữ là Python), hãy xác định tên module và đề xuất lệnh `pip install` trong khối ```bash ... ``` DUY NHẤT.
    *   Nếu lỗi do nguyên nhân khác (thiếu file, quyền, cú pháp sai, lệnh không tồn tại trong Batch/Shell/FortiOS CLI, cấu hình môi trường...) hoặc **ngôn ngữ không phải Python**, hãy đề xuất hành động người dùng cần làm thủ công. **KHÔNG đề xuất `pip install` cho ngôn ngữ không phải Python.**
d.  **Sửa lỗi Code:** Nếu lỗi có thể sửa trực tiếp trong mã **{language_name}**, hãy cung cấp phiên bản mã đã sửa lỗi trong khối ```{code_block_tag} ... ``` CUỐI CÙNG. Mã sửa lỗi phải cố gắng đáp ứng **Yêu cầu ban đầu của người dùng**. Nếu không thể sửa lỗi trong code (ví dụ: lỗi do môi trường, thiếu file...), hãy giải thích tại sao.

**QUAN TRỌNG:**
*   Trả về phần giải thích và đề xuất hành động (bằng Markdown) trước.
*   Nếu có lệnh cài đặt pip (chỉ cho Python), đặt nó trong khối ```bash ... ``` riêng.
*   Sau đó, nếu có thể sửa code, cung cấp khối mã ```{code_block_tag} ... ``` CUỐI CÙNG chứa code đã sửa. Không thêm lời dẫn hay giải thích nào khác sau khối mã này.
*   Nếu không sửa được code, chỉ cần giải thích và (nếu có) đề xuất hành động/cài đặt.

**Phân tích và đề xuất:**
"""
    return prompt

# Ham tao prompt yeu cau Gemini giai thich
def create_explain_prompt(content_to_explain, context, language=None):
    prompt_header = "Bạn là một trợ lý AI giỏi giải thích các khái niệm kỹ thuật một cách đơn giản, dễ hiểu cho người dùng không chuyên."
    prompt_instruction = "\n\n**Yêu cầu:** Giải thích nội dung sau đây bằng tiếng Việt, sử dụng Markdown, tập trung vào ý nghĩa chính và những điều người dùng cần biết. Giữ cho giải thích ngắn gọn và rõ ràng. Bắt đầu trực tiếp bằng nội dung giải thích, không thêm lời dẫn."
    context_description = ""
    language_name = get_language_name(language) if language else "nội dung"
    code_block_tag = language if language and language.isalnum() else 'code'
    if language == 'fortios': code_block_tag = 'fortios'


    try:
        if isinstance(content_to_explain, str) and content_to_explain.strip().startswith('{') and content_to_explain.strip().endswith('}'):
             parsed_json = json.loads(content_to_explain)
             content_to_explain_formatted = json.dumps(parsed_json, ensure_ascii=False, indent=2)
        else:
             content_to_explain_formatted = str(content_to_explain)
    except json.JSONDecodeError:
         content_to_explain_formatted = str(content_to_explain)

    if context == 'code':
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
    else:
         context_description = f"Nội dung cần giải thích:\n```\n{content_to_explain_formatted}\n```"

    full_prompt = rf"{prompt_header}{context_description}{prompt_instruction}" # Su dung raw f-string
    return full_prompt

# Ham goi Gemini API
def generate_response_from_gemini(full_prompt, model_config_param, is_for_review_or_debug=False):
    global GOOGLE_API_KEY
    ui_api_key = None

    if not isinstance(model_config_param, dict):
        app.logger.warning(f"model_config nhan dc ko phai dict, ma la {type(model_config_param)}. Dung dict rong.")
        model_config_internal = {}
    else:
        model_config_internal = model_config_param.copy()

    try:
        ui_api_key = model_config_internal.pop('api_key', None)
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
                 "```text"
             )
             first_meaningful_line = False
             for line in lines:
                 stripped_line_lower = line.strip().lower()
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
        app.logger.error(f"Loi API Gemini ({model_name_for_error}): {error_message}", exc_info=True)
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
             pass

# Ham trich xuat khoi ma
def extract_code_block(raw_text, requested_extension, user_input_for_context=""):
    primary_tags = [requested_extension]
    if requested_extension == 'py': primary_tags.append('python')
    if requested_extension == 'sh': primary_tags.extend(['bash', 'shell'])
    if requested_extension == 'bat': primary_tags.append('batch')
    if requested_extension == 'ps1': primary_tags.append('powershell')
    if requested_extension == 'fortios': primary_tags.extend(['cli', 'text']) # Allow 'cli' and 'text' as aliases for 'fortios'

    is_fortigate_request = "fortigate" in user_input_for_context.lower() or "fortios" in user_input_for_context.lower() or requested_extension == 'fortios'

    if is_fortigate_request:
        if 'fortios' not in primary_tags: primary_tags.insert(0, 'fortios')
        # If it's a FortiGate request and a generic extension like txt/conf is used, prioritize fortios tag.
        if requested_extension in ['txt', 'conf', 'cli', 'text', 'log']:
             if 'fortios' not in primary_tags: primary_tags.insert(0, 'fortios')
        # Add common alternatives for FortiOS if not already primary
        if 'cli' not in primary_tags: primary_tags.append('cli')
        if 'text' not in primary_tags: primary_tags.append('text')

    unique_primary_tags = []
    for tag in primary_tags:
        if tag and tag not in unique_primary_tags: # Ensure tag is not empty and unique
            unique_primary_tags.append(tag)

    app.logger.info(f"Trich xuat code voi tags: {unique_primary_tags} cho ext: .{requested_extension}")

    for tag in unique_primary_tags:
        # Pattern for ```lang ... ``` (with optional trailing info on first line)
        pattern_strict = r"```" + re.escape(tag) + r"(?:[^\S\n].*?)?\s*\n([\s\S]*?)\n```"
        # More flexible pattern if there's no newline after ```lang
        pattern_flexible = r"```" + re.escape(tag) + r"(?:[^\S\n].*?)?\s*([\s\S]*?)\s*```"

        for p_idx, pattern_str in enumerate([pattern_strict, pattern_flexible]):
            try:
                matches = list(re.finditer(pattern_str, raw_text, re.IGNORECASE)) # Use finditer to get all matches
                if matches:
                    app.logger.info(f"Tim thay khoi code voi tag: '{tag}' (pattern {p_idx}).")
                    return matches[-1].group(1).strip() # Return the last found block
            except re.error as re_err:
                app.logger.error(f"Loi Regex voi pattern '{pattern_str}' cho tag '{tag}': {re_err}")
                continue # Try next pattern or tag

    # Fallback to generic code block if specific tag not found
    # Try to find ```lang\n...``` or ```\n...```
    generic_matches = list(re.finditer(r"```(?:([\w\-\./\+]+)[^\S\n]*)?\s*\n([\s\S]*?)\n```", raw_text))
    if not generic_matches: # Try more flexible ```lang...``` or ```...```
        generic_matches = list(re.finditer(r"```(?:([\w\-\./\+]+)[^\S\n]*)?\s*([\s\S]*?)\s*```", raw_text))

    if generic_matches:
        last_block_match = generic_matches[-1] # Use the last generic block
        lang_tag_found = last_block_match.group(1)
        code_content = last_block_match.group(2).strip()

        if lang_tag_found:
            lang_tag_found = lang_tag_found.strip().lower()
            app.logger.warning(f"Tim thay khoi code chung voi hint '```{lang_tag_found}```. Dung cho '.{requested_extension}'.")
            # If this generic block's language hint matches the requested one, it's a good candidate
            if lang_tag_found == 'fortios' and is_fortigate_request: return code_content # Good match
            if lang_tag_found == requested_extension or \
               (requested_extension == 'py' and lang_tag_found == 'python') or \
               (requested_extension == 'sh' and lang_tag_found in ['bash', 'shell']) or \
               (requested_extension == 'bat' and lang_tag_found == 'batch') or \
               (requested_extension == 'ps1' and lang_tag_found == 'powershell'):
                return code_content
        else:
            app.logger.warning(f"Tim thay khoi code ```...``` (khong co hint ngon ngu). Gia su dung cho '.{requested_extension}'.")
        # If no language tag or doesn't match, but it's the only block, return its content
        return code_content

    # Last resort: if the text is short and doesn't contain typical markdown block delimiters,
    # it might be direct code output.
    lines = raw_text.splitlines()
    is_likely_direct_code = (
        len(lines) < 30 and # Arbitrary line limit
        not any(kw in raw_text.lower() for kw in ["response:", "here's", "this will", "explanation:", "note:", "```"]) and
        not raw_text.startswith("I am a large language model") # Common LLM preamble
    )
    if is_likely_direct_code:
        app.logger.warning(f"Ko tim thay khoi ```. Tra ve raw text vi giong code truc tiep cho '.{requested_extension}'. Raw: '{raw_text[:100]}...'")
        return raw_text.strip()

    app.logger.warning(f"Ko tim thay khoi code cho '.{requested_extension}' hoac khoi chung. Tra ve raw text. Raw: '{raw_text[:100]}...'")
    return raw_text.strip() # Return raw text if no block found, as a last resort

# Endpoint sinh code
@app.route('/api/generate', methods=['POST'])
def handle_generate():
    data = request.get_json()
    user_input = data.get('prompt')
    model_config = data.get('model_config', {})
    target_os_input = data.get('target_os', 'auto')
    file_type_input = data.get('file_type', 'py') # Expecting extension like 'py', 'bat', or filename 'script.py'

    if not user_input:
        return jsonify({"error": "Vui lòng nhập yêu cầu."}), 400

    backend_os_name = get_os_name(sys.platform)
    # Determine target OS: if 'fortios', use it. If 'auto', use backend's OS. Else, use provided.
    target_os_name = target_os_input if target_os_input.lower() == 'fortios' else \
                     (backend_os_name if target_os_input == 'auto' else target_os_input)

    # Determine file extension from file_type_input
    if '.' in file_type_input:
        file_extension = file_type_input.split('.')[-1].lower()
    else:
        file_extension = file_type_input.lower()

    # Ensure file_extension is valid, default to 'py'
    if not file_extension or not (file_extension.isalnum() or file_extension == 'fortios'): # fortios can be an extension
        file_extension = 'py'

    # Pass the original file_type_input (which could be 'my_script.py' or just 'py') to create_prompt
    # create_prompt will internally derive the extension for its logic.
    full_prompt = create_prompt(user_input, backend_os_name, target_os_name, file_type_input)
    raw_response = generate_response_from_gemini(full_prompt, model_config, is_for_review_or_debug=False)

    app.logger.info("-" * 20 + " RAW GEMINI RESPONSE (Generate) " + "-" * 20)
    app.logger.info(raw_response)
    app.logger.info("-" * 60)

    if raw_response and not raw_response.startswith("Lỗi"):
        # Determine the extension to use for code block extraction
        ext_for_extraction = file_extension
        if target_os_name.lower() == 'fortios' and file_extension in ['txt', 'conf', 'cli', 'log']: # If target is FortiOS and file is generic, expect 'fortios' tag
            ext_for_extraction = 'fortios'
        elif file_extension == 'fortios': # If file_extension itself is 'fortios'
             ext_for_extraction = 'fortios'
        
        generated_code = extract_code_block(raw_response, ext_for_extraction, user_input_for_context=user_input)

        # Determine the "type" of the generated code to send back to frontend
        # This helps frontend decide on syntax highlighting, etc.
        effective_generated_type = file_extension
        if ext_for_extraction == 'fortios': # If we extracted for 'fortios', the type is 'fortios'
            effective_generated_type = 'fortios'
        

        # Check if the extracted code is just the raw response and doesn't look like a code block
        is_likely_raw_text = (generated_code == raw_response) and not generated_code.strip().startswith("```")

        if not generated_code.strip() or is_likely_raw_text:
             # This case means extract_code_block returned the raw response because it couldn't find a block,
             # and the raw response itself doesn't start with ``` (meaning it's likely just text, not code).
             app.logger.error(f"AI ko tra ve khoi ma hop le. Phan hoi tho: {raw_response[:200]}...")
             return jsonify({"error": f"AI không trả về khối mã hợp lệ. Phản hồi nhận được bắt đầu bằng: '{raw_response[:50]}...'"}), 500
        else:
            # Basic check for potentially dangerous keywords (can be expanded)
            potentially_dangerous = ["rm ", "del ", "format ", "shutdown ", "reboot ", ":(){:|:&};:", "dd if=/dev/zero", "mkfs"]
            code_lower = generated_code.lower()
            detected_dangerous = [kw for kw in potentially_dangerous if kw in code_lower]
            if detected_dangerous:
                app.logger.warning(f"Canh bao: Ma tao ra chua tu khoa nguy hiem: {detected_dangerous}")
            return jsonify({"code": generated_code, "generated_for_type": effective_generated_type}) # Send the effective type
    elif raw_response: # Error string from Gemini
        status_code = 400 if ("Lỗi cấu hình" in raw_response or "Lỗi: Phản hồi bị chặn" in raw_response) else 500
        return jsonify({"error": raw_response}), status_code
    else: # Should not happen if generate_response_from_gemini always returns string
        return jsonify({"error": "Không thể tạo mã hoặc có lỗi không xác định xảy ra."}), 500

# Endpoint danh gia code
@app.route('/api/review', methods=['POST'])
def handle_review():
    data = request.get_json()
    code_to_review = data.get('code')
    model_config = data.get('model_config', {})
    file_type = data.get('file_type', 'py') # Expects 'py', 'sh', 'bat', 'fortios' etc.

    if not code_to_review:
        return jsonify({"error": "Không có mã nào để đánh giá."}), 400

    # Ensure language_extension is just the extension, not 'script.py'
    language_extension = file_type.split('.')[-1].lower() if '.' in file_type else file_type.lower()
    if not language_extension: language_extension = 'py' # Default if empty

    full_prompt = create_review_prompt(code_to_review, language_extension)
    review_text = generate_response_from_gemini(full_prompt, model_config, is_for_review_or_debug=True)

    if review_text and not review_text.startswith("Lỗi"):
        return jsonify({"review": review_text})
    elif review_text: # Error string from Gemini
        status_code = 400 if ("Lỗi cấu hình" in review_text or "Lỗi: Phản hồi bị chặn" in review_text) else 500
        return jsonify({"error": review_text}), status_code
    else:
        return jsonify({"error": "Không thể đánh giá mã hoặc có lỗi không xác định xảy ra."}), 500

# Ham thuc thi lenh FortiGate
def execute_fortigate_commands(commands_string, fortigate_config):
    if not fortigate_config or not isinstance(fortigate_config, dict):
        return {"output": "", "error": "Thiếu cấu hình FortiGate.", "return_code": -1}

    host = fortigate_config.get('ipHost')
    username = fortigate_config.get('username')
    password = fortigate_config.get('password', '') # Password can be empty for key-based auth
    port = fortigate_config.get('portSsh', '22')

    if not host or not username:
        return {"output": "", "error": "Thiếu IP/Hostname hoặc Username cho FortiGate.", "return_code": -1}

    try: port = int(port)
    except ValueError:
        return {"output": "", "error": f"Port SSH không hợp lệ: {port}", "return_code": -1}

    device = {
        'device_type': 'fortinet',
        'host': host,
        'username': username,
        'password': password,
        'port': port,
        'session_log': 'logs/netmiko_session.log', # Log Netmiko session
        'global_delay_factor': 2, # Adjust if commands take longer
        'conn_timeout': 30,       # Connection timeout
        'auth_timeout': 30,       # Authentication timeout
        'banner_timeout': 30,     # Banner timeout
    }

    output_str = ""
    error_str = ""
    return_code = 0

    # Split commands by newline, ignore empty lines and comments
    commands_list = [cmd.strip() for cmd in commands_string.splitlines() if cmd.strip() and not cmd.strip().startswith('#')]
    if not commands_list:
        return {"output": "Không có lệnh hợp lệ để thực thi.", "error": "", "return_code": 0}

    try:
        app.logger.info(f"Dang ket noi toi FortiGate: {host}:{port} voi user: {username}")
        with ConnectHandler(**device) as net_connect:
            app.logger.info("Ket noi FortiGate thanh cong.")

            # Determine if configuration commands are present
            is_config_mode_likely = any(cmd.startswith(("config ", "edit ", "set ", "unset ", "append ", "delete ")) for cmd in commands_list)
            
            if is_config_mode_likely:
                app.logger.info("Phat hien lenh config, su dung send_config_set.")
                # send_config_set is generally better for configuration changes
                # It handles entering and exiting config mode automatically.
                output_str = net_connect.send_config_set(commands_list, exit_config_mode=True, delay_factor=2, cmd_verify=False) # cmd_verify=False to avoid issues with FortiOS prompts
                
                # Check for common error indicators in FortiOS output
                if "Command fail" in output_str or "error" in output_str.lower() or "Invalid" in output_str:
                    # Avoid flagging single-line echo as error if it's just the command itself
                    if not (len(commands_list) == 1 and commands_list[0].strip() == output_str.strip()):
                        error_str = output_str # The output itself is the error
                        return_code = 1 # Generic error code for command failure
                        # Try to extract a more specific return code if FortiOS provides one (less common)
                        match_error_code = re.search(r"Return code (\-?\d+)", output_str)
                        if match_error_code:
                            try: return_code = int(match_error_code.group(1))
                            except ValueError: pass
                        app.logger.warning(f"Loi khi thuc thi config tren FortiGate: {output_str}")
                else:
                    app.logger.info(f"Ket qua config FortiGate:\n{output_str}")
            else:
                # For show/get/diagnose commands, send them one by one
                app.logger.info("Chi co lenh show/get/diagnose, su dung send_command cho tung lenh.")
                full_output = []
                for cmd in commands_list:
                    # Define a more specific prompt pattern if needed, or let Netmiko auto-detect.
                    # FortiOS prompt usually ends with '# ' or '$ ' (if in restricted mode)
                    # A more robust prompt pattern for FortiOS might be r"\(.+?\) # $" or similar
                    prompt_pattern_str = r"\(.+?\) # $" # Example: (global) #
                    current_output = net_connect.send_command(
                        cmd, 
                        delay_factor=2,
                        expect_string=prompt_pattern_str if re.search(prompt_pattern_str, net_connect.base_prompt) else None # Use pattern if base_prompt matches it
                    )
                    full_output.append(f"$ {cmd}\n{current_output}\n") # Prepend command for clarity
                    if "Command fail" in current_output or "command_cli_error" in current_output or "Unknown action" in current_output or "Invalid input" in current_output:
                         error_str += f"Loi khi chay '{cmd}': {current_output}\n"
                         return_code = 1 # Mark as error
                         match_error_code = re.search(r"Return code (\-?\d+)", current_output)
                         if match_error_code:
                            try: return_code = int(match_error_code.group(1))
                            except ValueError: pass
                output_str = "\n".join(full_output)
                if return_code == 0:
                    app.logger.info(f"Ket qua lenh FortiGate:\n{output_str}")
                else:
                    app.logger.warning(f"Loi khi thuc thi lenh FortiGate: {error_str}")


    except NetmikoTimeoutException as e:
        error_str = f"Lỗi Timeout khi kết nối hoặc thực thi lệnh trên FortiGate: {e}"
        return_code = -101 # Specific error code for timeout
        app.logger.error(error_str)
    except NetmikoAuthenticationException as e:
        error_str = f"Lỗi xác thực với FortiGate (sai Username/Password?): {e}"
        return_code = -102 # Specific error code for auth failure
        app.logger.error(error_str)
    except SSHException as e: # Bat SSHException tu paramiko
        error_str = f"Lỗi SSH khi kết nối FortiGate (Port SSH đúng? Firewall?): {e}"
        return_code = -103 # Specific error code for SSH general issues
        app.logger.error(error_str)
    except Exception as e:
        error_str = f"Lỗi không xác định khi thực thi lệnh FortiGate: {e}"
        return_code = -100 # Generic FortiGate execution error
        app.logger.error(error_str, exc_info=True)

    return {"output": output_str, "error": error_str, "return_code": return_code}


# Endpoint thuc thi code
@app.route('/api/execute', methods=['POST'])
def handle_execute():
    data = request.get_json()
    code_to_execute = data.get('code')
    run_as_admin = data.get('run_as_admin', False)
    file_type_requested = data.get('file_type', 'py') # Expects 'py', 'sh', 'bat', 'fortios' etc.
    fortigate_config = data.get('fortigate_config')   # For FortiOS commands

    if not code_to_execute:
        return jsonify({"error": "Không có mã nào để thực thi."}), 400

    backend_os = get_os_name(sys.platform)
    admin_warning = None
    temp_file_path = None
    command = []

    # Get just the extension part
    if '.' in file_type_requested:
         file_extension = file_type_requested.split('.')[-1].lower()
    else:
         file_extension = file_type_requested.lower()

    if not file_extension or not (file_extension.isalnum() or file_extension == 'fortios'): # fortios can be an extension
        file_extension = 'py' # Default if weird input

    
    # Handle FortiOS commands separately
    if file_extension == 'fortios':
        app.logger.info(f"Nhan dc lenh FortiOS CLI (.{file_extension}). Chuan bi thuc thi qua Netmiko.")
        if not fortigate_config:
            app.logger.error("Thieu thong tin fortigate_config de thuc thi lenh FortiOS.")
            return jsonify({
                "message": "Lỗi: Thiếu thông tin cấu hình FortiGate.",
                "output": "", "error": "Thiếu fortigate_config.", "return_code": -1,
                "executed_file_type": file_extension, "codeThatFailed": code_to_execute
            }), 400
        
        fgt_result = execute_fortigate_commands(code_to_execute, fortigate_config)
        
        response_message = "Gửi lệnh FortiOS CLI thành công." if fgt_result["return_code"] == 0 and not fgt_result["error"] else \
                           "Gửi lệnh FortiOS CLI hoàn tất (có thể có lỗi)."

        return jsonify({
            "message": response_message,
            "output": fgt_result["output"],
            "error": fgt_result["error"],
            "return_code": fgt_result["return_code"],
            "executed_file_type": file_extension, # Frontend uses this for display logic
            "codeThatFailed": code_to_execute
        })

    # For other script types, proceed with file-based execution
    app.logger.info(f"--- CANH BAO: Chuan bi thuc thi code file .{file_extension} (Admin/Root: {run_as_admin}) ---")

    try:
        # Create a temporary file with the correct extension
        # Ensure newline='' to prevent universal newlines mode from messing with script content, especially on Windows
        with tempfile.NamedTemporaryFile(mode='w', suffix=f'.{file_extension}', delete=False, encoding='utf-8', newline='') as temp_file:
            temp_file_path = temp_file.name
            temp_file.write(code_to_execute)
        app.logger.info(f"Da luu code vao file tam: {temp_file_path}")

        # Make executable on Linux/macOS for .sh and .py
        if backend_os in ["linux", "macos"] and file_extension in ['sh', 'py']:
            try:
                current_stat = os.stat(temp_file_path).st_mode
                os.chmod(temp_file_path, current_stat | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH) # Add execute for user, group, others
                app.logger.info(f"Da cap quyen thuc thi (chmod +x) cho: {temp_file_path}")
            except Exception as chmod_e:
                app.logger.error(f"Ko the cap quyen thuc thi file tam: {chmod_e}")
                # Proceed anyway, might still work if interpreter is called directly

        # Determine command based on OS and file type
        interpreter_path = sys.executable # Path to current python interpreter
        if file_extension == 'py':
            command = [interpreter_path, temp_file_path]
        elif file_extension == 'bat' and backend_os == 'windows':
            command = ['cmd', '/c', temp_file_path]
        elif file_extension == 'ps1' and backend_os == 'windows':
            # -NoProfile: Speeds up startup, avoids loading user profiles
            # -ExecutionPolicy Bypass: Allows running unsigned scripts for this session
            command = ['powershell', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', temp_file_path]
        elif file_extension == 'sh' and backend_os in ['linux', 'macos']:
             command = ['bash', temp_file_path] # Explicitly use bash for .sh files
        elif backend_os == 'windows': # Fallback for unknown extensions on Windows
            command = ['cmd', '/c', temp_file_path]
            app.logger.warning(f"Loai file '.{file_extension}' ko xd ro tren Windows, thu chay bang cmd /c.")
        elif backend_os in ['linux', 'macos']: # Fallback for unknown extensions on Unix-like
             command = ['bash', temp_file_path] # Attempt to run with bash
             app.logger.warning(f"Loai file '.{file_extension}' ko xd ro tren {backend_os}, thu chay bang bash.")
        else:
             # Should not happen if backend_os is one of the known types
             return jsonify({"error": f"Không hỗ trợ thực thi file .{file_extension} trên HĐH backend: {backend_os}"}), 501
        
        if run_as_admin:
            if backend_os == "windows":
                try:
                    is_admin = ctypes.windll.shell32.IsUserAnAdmin() != 0
                    if not is_admin:
                        # Cannot elevate directly from here easily without UAC prompt on backend.
                        # For now, just warn and run as normal user.
                        # True elevation would require a more complex setup or separate elevated service.
                        admin_warning = "Yeu cau Admin, nhung backend ko co quyen. Thuc thi quyen thuong."
                        app.logger.warning(f"{admin_warning}")
                    # If already admin, command will run with admin rights.
                except Exception as admin_check_e:
                    admin_warning = f"Ko the check admin ({admin_check_e}). Thuc thi quyen thuong."
                    app.logger.error(f"{admin_warning}")
            elif backend_os in ["linux", "darwin"]: # macOS is 'darwin'
                try:
                    # Check if sudo exists
                    subprocess.run(['which', 'sudo'], check=True, capture_output=True, text=True, errors='ignore')
                    app.logger.info("Them 'sudo'. Co the can nhap pass console backend.")
                    command.insert(0, 'sudo') # Prepend sudo to the command
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
        process_env["PYTHONIOENCODING"] = "utf-8" # Ensure Python scripts output UTF-8

        result = subprocess.run(
            command, capture_output=True, encoding='utf-8', errors='replace', # errors='replace' for robustness
            timeout=60, check=False, env=process_env, text=True # text=True for Python 3.7+
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
            "executed_file_type": file_extension, # Send back the actual extension executed
            "codeThatFailed": code_to_execute # For debugging purposes
        }
        if admin_warning: # Add warning to response if any
            response_data["warning"] = admin_warning
        return jsonify(response_data)

    except subprocess.TimeoutExpired:
        app.logger.error("Loi: Thuc thi file qua thoi gian (60s).")
        return jsonify({"error": "Thực thi file vượt quá thời gian cho phép.", "output": "", "error": "Timeout", "return_code": -1, "warning": admin_warning, "codeThatFailed": code_to_execute}), 408
    except FileNotFoundError as fnf_error: # E.g., 'bash' or 'powershell' not found
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
    original_prompt = data.get('prompt', '(Không có prompt gốc)') # Nhan prompt goc
    failed_code = data.get('code')
    stdout = data.get('stdout', '')
    stderr = data.get('stderr', '')
    model_config = data.get('model_config', {})
    file_type = data.get('file_type', 'py') # Expects 'py', 'sh', 'bat', 'fortios' etc.

    if not failed_code:
        return jsonify({"error": "Thiếu mã lỗi để gỡ rối."}), 400

    # Ensure language_extension is just the extension
    language_extension = file_type.split('.')[-1].lower() if '.' in file_type else file_type.lower()
    if not language_extension: language_extension = 'py'

    # Truyen original_prompt vao create_debug_prompt
    full_prompt = create_debug_prompt(original_prompt, failed_code, stdout, stderr, language_extension)
    raw_response = generate_response_from_gemini(full_prompt, model_config, is_for_review_or_debug=True)

    if raw_response and not raw_response.startswith("Lỗi"):
        explanation_part = raw_response
        corrected_code = None
        suggested_package = None

        # Extract pip install suggestion for Python only
        if language_extension == 'py':
            install_match = re.search(r"```bash\s*pip install\s+([\w\-==\.\+\[\]]+)\s*```", explanation_part, re.IGNORECASE)
            if install_match:
                suggested_package = install_match.group(1).strip()
                app.logger.info(f"Debug (Python): Phat hien de xuat package: {suggested_package}")
                # Remove the pip install block from the explanation part
                explanation_part = explanation_part[:install_match.start()].strip() + explanation_part[install_match.end():].strip()
        
        # Extract corrected code block (last one found with matching or generic tag)
        last_code_block_match = None
        debug_code_block_tag = language_extension if language_extension.isalnum() else 'code'
        # Try specific tags first, then generic 'code'
        patterns_to_try_tags = [debug_code_block_tag]
        if language_extension == 'py': patterns_to_try_tags.append('python')
        if language_extension == 'sh': patterns_to_try_tags.extend(['bash', 'shell'])
        if language_extension == 'bat': patterns_to_try_tags.append('batch')
        if language_extension == 'ps1': patterns_to_try_tags.append('powershell')
        if language_extension == 'fortios': patterns_to_try_tags.extend(['fortios', 'cli', 'text']) # For fortios

        unique_debug_tags = []
        for tag_val in patterns_to_try_tags:
            if tag_val and tag_val not in unique_debug_tags:
                unique_debug_tags.append(tag_val)
        if 'code' not in unique_debug_tags: unique_debug_tags.append('code') # Add generic as last resort


        for lang_tag_for_pattern in unique_debug_tags:
            pattern_strict = r"```" + re.escape(lang_tag_for_pattern) + r"(?:[^\S\n].*?)?\s*\n([\s\S]*?)\n```"
            pattern_flexible = r"```" + re.escape(lang_tag_for_pattern) + r"(?:[^\S\n].*?)?\s*([\s\S]*?)\s*```"
            if lang_tag_for_pattern == 'code': # Generic code block without language hint
                 pattern_strict = r"```\s*\n([\s\S]*?)\n```"
                 pattern_flexible = r"```\s*([\s\S]*?)\s*```"
            
            for p_idx, pattern_str in enumerate([pattern_strict, pattern_flexible]):
                try:
                    matches = list(re.finditer(pattern_str, explanation_part, re.IGNORECASE | re.MULTILINE))
                    if matches:
                        last_code_block_match = matches[-1] # Get the last match
                        app.logger.info(f"Debug: Tim thay code sua loi voi tag '{lang_tag_for_pattern}' (pattern {p_idx}).")
                        break 
                except re.error as re_err_debug:
                    app.logger.error(f"Loi Regex debug code sua voi pattern '{pattern_str}' tag '{lang_tag_for_pattern}': {re_err_debug}")
                    continue
            if last_code_block_match: break # Found a block, stop searching tags

        if last_code_block_match:
            start_index = last_code_block_match.start()
            # The part before the last code block is considered the explanation
            potential_explanation_before_code = explanation_part[:start_index].strip()
            if potential_explanation_before_code: # If there's text before the code block
                 explanation_part = potential_explanation_before_code
            else: # If AI only returned code, provide a placeholder explanation
                 explanation_part = f"(AI chỉ trả về code {get_language_name(language_extension)} đã sửa lỗi, không có giải thích)"
            corrected_code = last_code_block_match.group(1).strip()

        # Clean up common leading phrases from the explanation, if any
        explanation_part = re.sub(r"^(Phân tích và đề xuất:|Giải thích và đề xuất:|Phân tích:|Giải thích:)\s*", "", explanation_part, flags=re.IGNORECASE | re.MULTILINE).strip()

        return jsonify({
            "explanation": explanation_part if explanation_part else "(Không có giải thích)",
            "corrected_code": corrected_code,
            "suggested_package": suggested_package,
            "original_language": language_extension # Send back the language of the failed code
        })
    elif raw_response: # Error string from Gemini
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

    # Validate package_name (basic validation for safety)
    # Allows: letters, numbers, -, _, ==, ., +, [, ] (for extras like package[extra])
    if not re.fullmatch(r"^[a-zA-Z0-9\-_==\.\+\[\]]+$", package_name.replace('[','').replace(']','')): # Remove brackets for the regex character class
        app.logger.warning(f"Ten package ko hop le bi tu choi: {package_name}")
        return jsonify({"success": False, "error": f"Tên package không hợp lệ: {package_name}"}), 400

    app.logger.info(f"--- Chuan bi cai dat package: {package_name} ---")
    try:
        # Use shlex.split to handle complex package names (e.g., with version specifiers or extras)
        # but ensure each part is then passed as a separate argument to subprocess.run
        pip_command_parts = [sys.executable, '-m', 'pip', 'install'] + shlex.split(package_name)
        command = [part for part in pip_command_parts if part] # Filter out empty strings if any
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
            # Try to get a more specific error message from stderr
            detailed_error = error_output.strip().split('\n')[-1] if error_output.strip() else f"Lệnh Pip thất bại với mã trả về {return_code}."
            return jsonify({ "success": False, "message": message, "output": output, "error": detailed_error }), 500 # Return 500 for server-side failure

    except subprocess.TimeoutExpired:
        app.logger.error(f"Loi: Cai dat package '{package_name}' qua thoi gian (120s).")
        return jsonify({"success": False, "error": f"Timeout khi cài đặt '{package_name}'.", "output": "", "error": "Timeout"}), 408
    except FileNotFoundError: # sys.executable or pip not found
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
    context = data.get('context', 'unknown') # 'code', 'execution_result', 'review_text', 'debug_result', 'error_message', 'installation_result'
    model_config = data.get('model_config', {})
    file_type = data.get('file_type') # Optional, mainly for 'code' context, expects 'py', 'sh', 'bat', 'fortios'

    if not content_to_explain:
        return jsonify({"error": "Không có nội dung để giải thích."}), 400
    
    # Ensure content_to_explain is a string, pretty-print if it's a dict/list
    if isinstance(content_to_explain, dict) or isinstance(content_to_explain, list):
         try: content_to_explain = json.dumps(content_to_explain, ensure_ascii=False, indent=2)
         except Exception: content_to_explain = str(content_to_explain) # Fallback
    else:
        content_to_explain = str(content_to_explain) # Ensure it's a string

    # Normalize context, e.g. 'python_code' from older versions -> 'code'
    explain_context = 'code' if context == 'python_code' else context
    
    # Determine language for prompt (only relevant if context is 'code')
    language_for_prompt = file_type if explain_context == 'code' else None

    full_prompt = create_explain_prompt(content_to_explain, explain_context, language=language_for_prompt)
    explanation_text = generate_response_from_gemini(full_prompt, model_config, is_for_review_or_debug=True)

    if explanation_text and not explanation_text.startswith("Lỗi"):
        return jsonify({"explanation": explanation_text})
    elif explanation_text: # Error string from Gemini
        status_code = 400 if ("Lỗi cấu hình" in explanation_text or "Lỗi: Phản hồi bị chặn" in explanation_text) else 500
        return jsonify({"error": explanation_text}), status_code
    else:
        return jsonify({"error": "Không thể tạo giải thích hoặc có lỗi không xác định xảy ra."}), 500


if __name__ == '__main__':
    if not app.debug:
        import logging
        from logging.handlers import RotatingFileHandler
        if not os.path.exists('logs'):
            os.mkdir('logs')
        file_handler = RotatingFileHandler('logs/gemini_executor.log', maxBytes=102400, backupCount=10)
        file_handler.setFormatter(logging.Formatter(
            '%(asctime)s %(levelname)s: %(message)s [in %(pathname)s:%(lineno)d]'
        ))
        file_handler.setLevel(logging.INFO) # Log INFO and above to file
        app.logger.addHandler(file_handler)
        app.logger.setLevel(logging.INFO) # Set app logger level

    app.logger.info('Backend Gemini UI Executor dang khoi dong...')
    print("Backend đang chạy tại http://localhost:5001")
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