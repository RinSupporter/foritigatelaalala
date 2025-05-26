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
import ctypes # Dùng cho việc kiểm tra quyền admin trên Windows
import traceback # Để ghi log lỗi chi tiết
import json
import tempfile
import stat # cho chmod
from netmiko import ConnectHandler
from netmiko.exceptions import NetmikoTimeoutException, NetmikoAuthenticationException

# Tải biến môi trường từ file .env ở thư mục gốc
load_dotenv(dotenv_path='../.env')

app = Flask(__name__)
# Cho phép CORS từ frontend (chạy trên cổng 5173)
CORS(app, resources={r"/api/*": {"origins": "http://localhost:5173"}})

# --- Cấu hình Gemini ---
GOOGLE_API_KEY = os.getenv('GOOGLE_API_KEY')

# --- Ánh xạ cài đặt an toàn (KHÔNG THAY ĐỔI) ---
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

# clean tên HĐH
def get_os_name(platform_str):
    if platform_str == "win32": return "windows"
    if platform_str == "darwin": return "macos"
    return "linux"

# Helper ánh xạ extension sang tên ngôn ngữ thân thiện
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
    if ext_lower == 'fortigatecli' or ext_lower == 'fgtcli': return 'FortiGate CLI'
    return f'file .{ext_lower}'

# Hàm tạo prompt để yêu cầu Gemini sinh code
def create_prompt(user_input, backend_os_name, target_os_name, file_type, fortigate_rules_context=None): # Thêm fortigate_rules_context
    file_extension = ""
    file_type_description = ""
    if file_type and '.' in file_type:
        file_extension = file_type.split('.')[-1].lower()
        file_type_description = f"một file có tên `{file_type}`"
    elif file_type:
        file_extension = file_type.lower()
        file_type_description = f"một file loại `.{file_extension}` ({get_language_name(file_extension)})"
    else:
        file_extension = "py"
        file_type_description = f"một script Python (`.{file_extension}`)"

    code_block_tag = file_extension if file_extension and file_extension.isalnum() else 'code'
    if file_extension == "fortigatecli": # Dùng tag đặc biệt cho fortigate
        code_block_tag = "fortigatecli"
        file_type_description = "các lệnh FortiGate CLI"
        target_os_name = "FortiGateOS" # Cho Gemini biết mục tiêu

    prompt_header = f"""
Bạn là một trợ lý AI chuyên tạo mã nguồn hoặc lệnh CLI để thực thi các tác vụ dựa trên yêu cầu của người dùng.
**Môi trường Backend:** Máy chủ đang chạy {backend_os_name}.
**Mục tiêu Người dùng:** Tạo mã/lệnh phù hợp cho **{file_type_description}** và chạy trên hệ điều hành/thiết bị **{target_os_name}**.

**YÊU CẦU TUYỆT ĐỐI:**
1.  **PHẢN HỒI CỦA BẠN *CHỈ* ĐƯỢC PHÉP CHỨA KHỐI MÃ/LỆNH.**
2.  Khối mã/lệnh phải được bao trong dấu ```{code_block_tag} ... ```.
3.  **TUYỆT ĐỐI KHÔNG** bao gồm bất kỳ văn bản nào khác bên ngoài cặp dấu ```{code_block_tag} ... ```.
4.  Đảm bảo mã/lệnh là **an toàn** và **chỉ thực hiện đúng yêu cầu**.
"""
    if file_extension == "fortigatecli":
        prompt_specifics = f"""
5.  Đối với FortiGate CLI:
    *   Luôn bắt đầu bằng `config <scope>` (ví dụ: `config firewall policy`).
    *   Sử dụng `edit <id>` để sửa hoặc `edit 0` để tạo mới (nếu ID không quan trọng hoặc là 0 cho auto).
    *   Sử dụng các lệnh `set` phù hợp.
    *   Sử dụng `next` để lưu một mục và chuyển sang mục tiếp theo (nếu tạo/sửa nhiều).
    *   Kết thúc bằng `end` để thoát khỏi `config <scope>`.
    *   Nếu yêu cầu là chỉnh sửa rule dựa trên tên, hãy cố gắng xác định `policyid` từ ngữ cảnh rules được cung cấp (nếu có).
{f"**Ngữ cảnh Rules FortiGate hiện tại (để tham khảo nếu cần tìm ID):**\n```\n{fortigate_rules_context}\n```" if fortigate_rules_context else ""}
"""
    else:
        prompt_specifics = f"""
5.  Nếu là script (Python, Shell, PowerShell, Batch):
    *   Sử dụng `try-except` (hoặc cách xử lý lỗi tương đương) để xử lý lỗi cơ bản nếu có thể.
    *   In thông báo kết quả hoặc lỗi ra `stdout` hoặc `stderr`.
    *   Đối với Python, đảm bảo tương thích Python 3.
    *   Đối với Shell, ưu tiên cú pháp tương thích `bash`.
6.  LUÔN LUÔN có cơ chế thông báo kết quả của code (ví dụ: if else)
7.  Chú ý và xem xét xem loại file đó khi chạy có hỗ trợ tiếng việt không, nếu có thì hãy ghi kết quả trả về bằng tiếng việt có dấu, nếu không thì hãy ghi không dấu để tránh rối loạn ký tự trong output.
"""

    prompt_footer = f"""
**(Nhắc lại)** Chỉ cung cấp khối mã/lệnh cuối cùng cho **{file_type_description}** trên **{target_os_name}** trong cặp dấu ```{code_block_tag} ... ```.

**Yêu cầu của người dùng:** "{user_input}"

**Khối mã/lệnh:**
"""
    return prompt_header + prompt_specifics + prompt_footer


# Hàm tạo prompt để yêu cầu Gemini đánh giá code
def create_review_prompt(code_to_review, language):
    language_name = get_language_name(language)
    code_block_tag = language if language and language.isalnum() else 'code'
    if language == "fortigatecli": code_block_tag = "fortigatecli"


    prompt = f"""
Bạn là một chuyên gia đánh giá code/lệnh **{language_name}**. Hãy phân tích đoạn mã/lệnh **{language_name}** sau đây và đưa ra nhận xét về:
1.  **Độ an toàn:** Liệu mã/lệnh có chứa các lệnh nguy hiểm không? Rủi ro?
2.  **Tính đúng đắn:** Mã/lệnh có thực hiện đúng yêu cầu dự kiến không? Có lỗi cú pháp hoặc logic nào không?
3.  **Tính hiệu quả/Tối ưu:** Có cách viết tốt hơn, ngắn gọn hơn hoặc hiệu quả hơn trong **{language_name}** không?
4.  **Không cần đưa code cải tiến**

Đoạn mã/lệnh **{language_name}** cần đánh giá:
```{code_block_tag}
{code_to_review}
```

**QUAN TRỌNG:** Chỉ trả về phần văn bản nhận xét/đánh giá bằng Markdown. Bắt đầu trực tiếp bằng nội dung đánh giá. Định dạng các khối mã ví dụ (nếu có) trong Markdown bằng ```{code_block_tag} ... ```. Kết thúc bằng dòng 'Mức độ an toàn: An toàn/Ổn/Nguy hiểm'.
"""
    return prompt

# Hàm tạo prompt để yêu cầu Gemini gỡ lỗi code
def create_debug_prompt(original_prompt, failed_code, stdout, stderr, language):
    language_name = get_language_name(language)
    code_block_tag = language if language and language.isalnum() else 'code'
    if language == "fortigatecli": code_block_tag = "fortigatecli"


    prompt = f"""
Bạn là một chuyên gia gỡ lỗi **{language_name}**. Người dùng đã cố gắng chạy một đoạn mã/lệnh **{language_name}** dựa trên yêu cầu ban đầu của họ, nhưng đã gặp lỗi.

**1. Yêu cầu ban đầu của người dùng:**
{original_prompt}

**2. Đoạn mã/lệnh {language_name} đã chạy và gây lỗi:**
```{code_block_tag}
{failed_code}
```

**3. Kết quả Output (stdout) khi chạy mã/lệnh:**
```
{stdout if stdout else "(Không có output)"}
```

**4. Kết quả Lỗi (stderr) khi chạy mã/lệnh:**
```
{stderr if stderr else "(Không có lỗi stderr)"}
```

**Nhiệm vụ của bạn:**
a.  **Phân tích:** Xác định nguyên nhân chính xác gây ra lỗi.
b.  **Giải thích:** Cung cấp một giải thích rõ ràng, ngắn gọn về lỗi cho người dùng bằng Markdown.
c.  **Đề xuất Hành động / Cài đặt:**
    *   **QUAN TRỌNG (CHỈ CHO PYTHON):** Nếu lỗi là `ModuleNotFoundError` (và ngôn ngữ là Python), hãy xác định tên module và đề xuất lệnh `pip install` trong khối ```bash ... ``` DUY NHẤT.
    *   Nếu lỗi do nguyên nhân khác hoặc **ngôn ngữ không phải Python**, hãy đề xuất hành động người dùng cần làm thủ công.
d.  **Sửa lỗi Code/Lệnh:** Nếu lỗi có thể sửa trực tiếp trong mã/lệnh **{language_name}**, hãy cung cấp phiên bản đã sửa lỗi trong khối ```{code_block_tag} ... ``` CUỐI CÙNG.

**QUAN TRỌNG:**
*   Trả về phần giải thích và đề xuất hành động (bằng Markdown) trước.
*   Nếu có lệnh cài đặt pip (chỉ cho Python), đặt nó trong khối ```bash ... ``` riêng.
*   Sau đó, nếu có thể sửa code/lệnh, cung cấp khối mã ```{code_block_tag} ... ``` CUỐI CÙNG. Không thêm lời dẫn hay giải thích nào khác sau khối mã này.

**Phân tích và đề xuất:**
"""
    return prompt


# Hàm tạo prompt để yêu cầu Gemini giải thích
def create_explain_prompt(content_to_explain, context, language=None):
    prompt_header = "Bạn là một trợ lý AI giỏi giải thích các khái niệm kỹ thuật một cách đơn giản, dễ hiểu cho người dùng không chuyên."
    prompt_instruction = "\n\n**Yêu cầu:** Giải thích nội dung sau đây bằng tiếng Việt, sử dụng Markdown, tập trung vào ý nghĩa chính và những điều người dùng cần biết. Giữ cho giải thích ngắn gọn và rõ ràng. Bắt đầu trực tiếp bằng nội dung giải thích, không thêm lời dẫn."
    context_description = ""
    language_name = get_language_name(language) if language else "nội dung"
    code_block_tag = language if language and language.isalnum() else 'code'
    if language == "fortigatecli": code_block_tag = "fortigatecli"


    try:
        if isinstance(content_to_explain, str) and content_to_explain.strip().startswith('{') and content_to_explain.strip().endswith('}'):
             parsed_json = json.loads(content_to_explain)
             content_to_explain_formatted = json.dumps(parsed_json, ensure_ascii=False, indent=2)
        else:
             content_to_explain_formatted = str(content_to_explain)
    except json.JSONDecodeError:
         content_to_explain_formatted = str(content_to_explain)

    if context == 'code':
        context_description = f"Đây là một đoạn mã/lệnh **{language_name}**:\n```{code_block_tag}\n{content_to_explain_formatted}\n```"
        prompt_instruction = f"\n\n**Yêu cầu:** Giải thích đoạn mã/lệnh **{language_name}** này làm gì, mục đích chính của nó là gì, và tóm tắt các bước thực hiện chính (nếu có). Trả lời bằng tiếng Việt, sử dụng Markdown. Bắt đầu trực tiếp bằng nội dung giải thích."
    elif context == 'execution_result':
        context_description = f"Đây là kết quả sau khi thực thi một đoạn mã/lệnh:\n```json\n{content_to_explain_formatted}\n```"
        prompt_instruction = "\n\n**Yêu cầu:** Phân tích kết quả thực thi này (stdout, stderr, mã trả về). Cho biết lệnh có vẻ đã thành công hay thất bại và giải thích ngắn gọn tại sao dựa trên kết quả. Lưu ý cả các cảnh báo (warning) nếu có. Trả lời bằng tiếng Việt, sử dụng Markdown. Bắt đầu trực tiếp bằng nội dung giải thích."
    elif context == 'review_text':
        context_description = f"Đây là một bài đánh giá code/lệnh:\n```markdown\n{content_to_explain_formatted}\n```"
        prompt_instruction = "\n\n**Yêu cầu:** Tóm tắt và giải thích những điểm chính của bài đánh giá này bằng ngôn ngữ đơn giản hơn. Trả lời bằng tiếng Việt, sử dụng Markdown. Bắt đầu trực tiếp bằng nội dung giải thích."
    elif context == 'debug_result':
        debug_language = language
        if isinstance(content_to_explain, str):
            try:
                parsed_debug = json.loads(content_to_explain)
                debug_language = parsed_debug.get('original_language', language)
            except json.JSONDecodeError: pass
        elif isinstance(content_to_explain, dict):
             debug_language = content_to_explain.get('original_language', language)

        language_name = get_language_name(debug_language) if debug_language else "code/lệnh"
        context_description = f"Đây là kết quả từ việc gỡ lỗi một đoạn mã/lệnh {language_name}:\n```json\n{content_to_explain_formatted}\n```"
        prompt_instruction = f"\n\n**Yêu cầu:** Giải thích kết quả gỡ lỗi này, bao gồm nguyên nhân lỗi được xác định, ý nghĩa của đề xuất cài đặt package (nếu có và chỉ cho Python), và mục đích của đoạn code/lệnh {language_name} đã sửa (nếu có). Trả lời bằng tiếng Việt, sử dụng Markdown. Bắt đầu trực tiếp bằng nội dung giải thích."
    elif context == 'error_message':
        context_description = f"Đây là một thông báo lỗi:\n```\n{content_to_explain_formatted}\n```"
        prompt_instruction = "\n\n**Yêu cầu:** Giải thích thông báo lỗi này có nghĩa là gì, nguyên nhân phổ biến có thể gây ra nó, và gợi ý hướng khắc phục (nếu có thể). Trả lời bằng tiếng Việt, sử dụng Markdown. Bắt đầu trực tiếp bằng nội dung giải thích."
    elif context == 'installation_result':
        context_description = f"Đây là kết quả sau khi cài đặt một package Python:\n```json\n{content_to_explain_formatted}\n```"
        prompt_instruction = "\n\n**Yêu cầu:** Phân tích kết quả cài đặt package này. Cho biết việc cài đặt thành công hay thất bại, và giải thích ngắn gọn output/error từ pip. Trả lời bằng tiếng Việt, sử dụng Markdown. Bắt đầu trực tiếp bằng nội dung giải thích."
    else:
         context_description = f"Nội dung cần giải thích:\n```\n{content_to_explain_formatted}\n```"

    full_prompt = f"{prompt_header}{context_description}{prompt_instruction}"
    return full_prompt


# Hàm gọi Gemini API
def generate_response_from_gemini(full_prompt, model_config, is_for_review_or_debug=False):
    global GOOGLE_API_KEY
    ui_api_key = None

    try:
        ui_api_key = model_config.pop('api_key', None)
        if ui_api_key and not ui_api_key.strip():
            ui_api_key = None
        effective_api_key = ui_api_key if ui_api_key else GOOGLE_API_KEY
        if not effective_api_key:
            return "Lỗi cấu hình: Thiếu API Key."

        try:
            genai.configure(api_key=effective_api_key)
        except Exception as config_e:
             key_source = "giao diện" if ui_api_key else ".env"
             error_detail = str(config_e)
             if "API key not valid" in error_detail: return f"Lỗi cấu hình: API key từ {key_source} không hợp lệ."
             else: return f"Lỗi cấu hình: Không thể cấu hình Gemini với API key từ {key_source} ({error_detail})."

        model_name = model_config.get('model_name', 'gemini-1.5-flash')
        temperature = model_config.get('temperature', 0.7)
        top_p = model_config.get('top_p', 0.95)
        top_k = model_config.get('top_k', 40)
        safety_setting_key = model_config.get('safety_setting', 'BLOCK_MEDIUM_AND_ABOVE')
        safety_settings = SAFETY_SETTINGS_MAP.get(safety_setting_key, SAFETY_SETTINGS_MAP['BLOCK_MEDIUM_AND_ABOVE'])

        generation_config = GenerationConfig(temperature=float(temperature), top_p=float(top_p), top_k=int(top_k))
        model = genai.GenerativeModel(model_name=model_name)
        response = model.generate_content(full_prompt, generation_config=generation_config, safety_settings=safety_settings)

        if not response.candidates and hasattr(response, 'prompt_feedback') and response.prompt_feedback.block_reason:
            block_reason = response.prompt_feedback.block_reason.name
            return f"Lỗi: Phản hồi bị chặn bởi cài đặt an toàn (Lý do: {block_reason})."

        raw_text = response.text.strip()
        if is_for_review_or_debug and raw_text:
             lines = raw_text.splitlines()
             cleaned_lines = []
             prefixes_to_remove = ("đây là đánh giá", "here is the review", "phân tích code", "review:", "analysis:", "đây là phân tích", "here is the analysis", "giải thích và đề xuất:", "phân tích và đề xuất:", "đây là giải thích", "here is the explanation", "giải thích:", "explanation:", "[thinking", "[processing", "```text")
             first_meaningful_line = False
             for line in lines:
                 stripped_line_lower = line.strip().lower()
                 if not first_meaningful_line and any(stripped_line_lower.startswith(p) for p in prefixes_to_remove): continue
                 if line.strip(): first_meaningful_line = True
                 if first_meaningful_line: cleaned_lines.append(line)
             final_text = "\n".join(cleaned_lines).strip()
             return final_text
        return raw_text
    except Exception as e:
        error_message = str(e)
        traceback.print_exc(file=sys.stderr)
        if "API key not valid" in error_message: return f"Lỗi cấu hình: API key không hợp lệ."
        # Các xử lý lỗi khác ...
        return f"Lỗi máy chủ khi gọi Gemini: {error_message}"
    finally:
        if ui_api_key and GOOGLE_API_KEY and GOOGLE_API_KEY != ui_api_key:
            try: genai.configure(api_key=GOOGLE_API_KEY)
            except Exception: pass


# Hàm trích xuất khối mã
def extract_code_block(raw_text, requested_extension):
    primary_tags = [requested_extension]
    if requested_extension == 'py': primary_tags.append('python')
    if requested_extension == 'sh': primary_tags.append('bash')
    if requested_extension == 'bat': primary_tags.append('batch')
    if requested_extension == 'ps1': primary_tags.append('powershell')
    if requested_extension == 'fortigatecli': primary_tags.append('fgtcli') # Thêm alias

    for tag in primary_tags:
        pattern = r"```" + re.escape(tag) + r"\s*([\s\S]*?)\s*```"
        matches = list(re.finditer(pattern, raw_text, re.IGNORECASE))
        if matches: return matches[-1].group(1).strip()

    matches_generic = list(re.finditer(r"```\s*([\s\S]*?)\s*```", raw_text))
    if matches_generic: return matches_generic[-1].group(1).strip()
    return raw_text.strip()


# Endpoint để sinh code (cho file script)
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
    if not file_extension or not file_extension.isalnum(): file_extension = 'py'

    # Nếu yêu cầu là cho fortigate, dùng endpoint riêng
    if "fortigate" in user_input.lower() or "fgt" in user_input.lower() or file_extension == "fortigatecli":
        return handle_fortigate_generate_script_internal(data)


    full_prompt = create_prompt(user_input, backend_os_name, target_os_name, file_type_input)
    raw_response = generate_response_from_gemini(full_prompt, model_config.copy())

    if raw_response and not raw_response.startswith("Lỗi"):
        generated_code = extract_code_block(raw_response, file_extension)
        is_likely_raw_text = (generated_code == raw_response) and not generated_code.strip().startswith("```")
        if not generated_code.strip() or is_likely_raw_text:
             return jsonify({"error": f"AI không trả về khối mã hợp lệ. Phản hồi: '{raw_response[:50]}...'"}), 500
        else:
            return jsonify({"code": generated_code, "generated_for_type": file_extension})
    elif raw_response:
        return jsonify({"error": raw_response}), 400 if "Lỗi cấu hình" in raw_response else 500
    else:
        return jsonify({"error": "Không thể tạo mã."}), 500

# Hàm lấy thông tin kết nối FortiGate
def get_fortigate_connection_params(data):
    fgt_config = data.get('fortigate_config')
    if not fgt_config or not fgt_config.get('ipHost') or not fgt_config.get('username'):
        raise ValueError("Thiếu thông tin kết nối FortiGate (IP/Hostname, Username).")
    return {
        'device_type': 'fortinet',
        'host': fgt_config['ipHost'],
        'port': fgt_config.get('portSsh', 22), # Mặc định port 22
        'username': fgt_config['username'],
        'password': fgt_config.get('password', ''),
        'timeout': 20,  # Tăng timeout
        'auth_timeout': 30, # Timeout xác thực
        'banner_timeout': 20, # Timeout banner
        # 'session_log': 'netmiko_session.log' # Bỏ comment để debug
    }

# Endpoint để tạo script FortiGate
# Được gọi nội bộ từ handle_generate nếu phát hiện yêu cầu FortiGate
# hoặc có thể gọi trực tiếp từ frontend nếu muốn tách bạch
def handle_fortigate_generate_script_internal(req_data):
    user_input = req_data.get('prompt')
    model_config_data = req_data.get('model_config', {})
    # fortigate_config = req_data.get('fortigate_config') # Biến này đã được lấy trong get_fortigate_connection_params

    if not user_input:
        return jsonify({"error": "Vui lòng nhập yêu cầu cho FortiGate."}), 400

    rules_context = ""
    # Bước 1: (Tùy chọn) Lấy rules hiện tại từ FortiGate làm context
    # Hiện tại bỏ qua bước này để đơn giản, AI sẽ tạo script dựa trên prompt thuần
    # Nếu muốn lấy rules, bạn sẽ cần kết nối SSH ở đây
    # try:
    #     device_params = get_fortigate_connection_params(req_data)
    #     with ConnectHandler(**device_params) as net_connect:
    #         # net_connect.enable() # FortiGate thường không cần enable riêng
    #         rules_context = net_connect.send_command("show firewall policy", read_timeout=30)
    # except ValueError as ve: # Lỗi thiếu config
    #      return jsonify({"error": f"Lỗi cấu hình FortiGate: {str(ve)}"}), 400
    # except (NetmikoTimeoutException, NetmikoAuthenticationException) as conn_err:
    #      print(f"Loi ket noi FGT khi lay rule: {conn_err}") # Log loi
    #      # Khong tra ve loi ngay, AI van co the tao script
    #      rules_context = f"(Không thể lấy rules hiện tại: {str(conn_err)})"
    # except Exception as e:
    #      print(f"Loi khac khi lay rule FGT: {e}")
    #      rules_context = f"(Lỗi không xác định khi lấy rules: {str(e)})"


    # Bước 2: Tạo prompt cho Gemini
    backend_os_name = get_os_name(sys.platform)
    # file_type "fortigatecli" sẽ được xử lý đặc biệt trong create_prompt
    full_prompt = create_prompt(user_input, backend_os_name, "FortiGateOS", "fortigatecli", fortigate_rules_context=rules_context)
    raw_response = generate_response_from_gemini(full_prompt, model_config_data.copy())


    if raw_response and not raw_response.startswith("Lỗi"):
        generated_script = extract_code_block(raw_response, "fortigatecli") # Quan trọng: dùng đúng extension
        is_likely_raw_text = (generated_script == raw_response) and not generated_script.strip().startswith("```")

        if not generated_script.strip() or is_likely_raw_text:
             return jsonify({"error": f"AI không trả về khối lệnh FortiGate CLI hợp lệ. Phản hồi: '{raw_response[:100]}...'"}), 500
        else:
            # generated_for_type nên là 'fortigatecli'
            return jsonify({"code": generated_script, "generated_for_type": "fortigatecli"})
    elif raw_response:
        return jsonify({"error": raw_response}), 400 if "Lỗi cấu hình" in raw_response else 500
    else:
        return jsonify({"error": "Không thể tạo script FortiGate."}), 500

# Endpoint riêng cho việc tạo script FortiGate nếu frontend muốn gọi trực tiếp
@app.route('/api/fortigate_generate_script', methods=['POST'])
def route_fortigate_generate_script():
    return handle_fortigate_generate_script_internal(request.get_json())


# Endpoint để thực thi script trên FortiGate
@app.route('/api/fortigate_execute_script', methods=['POST'])
def handle_fortigate_execute_script():
    data = request.get_json()
    script_to_execute = data.get('code') # Chuỗi lệnh CLI

    if not script_to_execute:
        return jsonify({"error": "Không có script FortiGate để thực thi."}), 400

    try:
        device_params = get_fortigate_connection_params(data)
    except ValueError as ve:
        return jsonify({"error": str(ve)}), 400
    except Exception as e_cfg: # Bắt các lỗi không mong muốn khác khi lấy params
        return jsonify({"error": f"Lỗi xử lý cấu hình FortiGate: {str(e_cfg)}"}), 400


    try:
        with ConnectHandler(**device_params) as net_connect:
            # FortiGate thường không cần enable riêng
            # Tách script thành danh sách các lệnh, loại bỏ dòng trống
            commands = [cmd.strip() for cmd in script_to_execute.strip().split('\n') if cmd.strip()]
            if not commands:
                return jsonify({"error": "Script rỗng, không có lệnh để gửi."}), 400

            # Gửi từng lệnh nếu là lệnh config, hoặc dùng send_config_set
            # send_config_set tốt hơn vì nó xử lý vào/ra config mode
            # Tuy nhiên, để linh hoạt hơn nếu script có cả lệnh show, ta có thể gửi tuần tự
            output_parts = []
            if commands[0].strip().lower().startswith("config"):
                # Nếu là khối config, dùng send_config_set
                output = net_connect.send_config_set(commands, exit_config_mode=True, read_timeout=60) # tang timeout
                output_parts.append(output if output else "[send_config_set không có output cụ thể]")
            else:
                # Nếu là lệnh đơn (thường là show), gửi từng lệnh
                for cmd in commands:
                    output = net_connect.send_command(cmd, read_timeout=60) # tang timeout
                    output_parts.append(f"$ {cmd}\n{output}")

        return jsonify({
            "message": "Đã gửi lệnh tới FortiGate.",
            "output": "\n".join(output_parts),
            "error": "",
            "return_code": 0,
            "executed_file_type": "fortigatecli" # Cho frontend biết
        })
    except (NetmikoTimeoutException, NetmikoAuthenticationException) as conn_err:
        return jsonify({"error": f"Lỗi kết nối/xác thực FortiGate: {str(conn_err)}"}), 500
    except Exception as e:
        traceback.print_exc(file=sys.stderr) # Log lỗi chi tiết ở backend
        return jsonify({"error": f"Lỗi không xác định khi thực thi trên FortiGate: {str(e)}"}), 500


# Endpoint để đánh giá code
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
    review_text = generate_response_from_gemini(full_prompt, model_config.copy(), is_for_review_or_debug=True)

    if review_text and not review_text.startswith("Lỗi"):
        return jsonify({"review": review_text})
    elif review_text:
        return jsonify({"error": review_text}), 400 if "Lỗi cấu hình" in review_text else 500
    else:
        return jsonify({"error": "Không thể đánh giá mã."}), 500

# Endpoint để thực thi code (cho file script)
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

    # Nếu là fortigatecli, không thực thi như file
    if file_extension == 'fortigatecli':
        return jsonify({"error": "Lệnh FortiGate CLI cần được thực thi qua endpoint /api/fortigate_execute_script.", "return_code": -1}), 400


    try:
        with tempfile.NamedTemporaryFile(mode='w', suffix=f'.{file_extension}', delete=False, encoding='utf-8', newline='') as temp_file:
            temp_file_path = temp_file.name
            temp_file.write(code_to_execute)

        if backend_os in ["linux", "macos"] and file_extension in ['sh', 'py']:
            try:
                current_stat = os.stat(temp_file_path).st_mode
                os.chmod(temp_file_path, current_stat | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
            except Exception as chmod_e: print(f"[LỖI] chmod: {chmod_e}")


        interpreter_path = sys.executable
        if file_extension == 'py': command = [interpreter_path, temp_file_path]
        elif file_extension == 'bat' and backend_os == 'windows': command = ['cmd', '/c', temp_file_path]
        elif file_extension == 'ps1' and backend_os == 'windows': command = ['powershell', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', temp_file_path]
        elif file_extension == 'sh' and backend_os in ['linux', 'macos']: command = ['bash', temp_file_path]
        elif backend_os == 'windows': command = ['cmd', '/c', temp_file_path]
        elif backend_os in ['linux', 'macos']: command = ['bash', temp_file_path]
        else: return jsonify({"error": f"Không hỗ trợ thực thi file .{file_extension} trên {backend_os}"}), 501

        if run_as_admin:
            if backend_os == "windows":
                try:
                    if not (ctypes.windll.shell32.IsUserAnAdmin() != 0): admin_warning = "Y/c Admin, nhưng backend ko có quyền. Chạy quyền thường."
                except Exception as admin_check_e: admin_warning = f"Ko check đc admin ({admin_check_e}). Chạy quyền thường."
            elif backend_os in ["linux", "darwin"]:
                try:
                    subprocess.run(['which', 'sudo'], check=True, capture_output=True, text=True)
                    command.insert(0, 'sudo')
                except: admin_warning = "Y/c Root, ko thấy 'sudo'. Chạy quyền thường."
            else: admin_warning = f"Y/c Admin/Root ko hỗ trợ rõ trên {backend_os}. Chạy quyền thường."
            if admin_warning: print(f"[CẢNH BÁO] {admin_warning}")


        process_env = os.environ.copy()
        process_env["PYTHONIOENCODING"] = "utf-8"
        result = subprocess.run(command, capture_output=True, encoding='utf-8', errors='replace', timeout=60, check=False, env=process_env, text=True)

        message = "Thực thi file thành công." if result.returncode == 0 else "Thực thi file hoàn tất (có thể có lỗi)."
        response_data = {"message": message, "output": result.stdout, "error": result.stderr, "return_code": result.returncode, "executed_file_type": file_extension, "codeThatFailed": code_to_execute}
        if admin_warning: response_data["warning"] = admin_warning
        return jsonify(response_data)

    except subprocess.TimeoutExpired:
        return jsonify({"error": "Thực thi file quá thời gian.", "output": "", "error": "Timeout", "return_code": -1, "warning": admin_warning, "codeThatFailed": code_to_execute}), 408
    except FileNotFoundError as fnf_error:
        return jsonify({"error": f"Lỗi: Không tìm thấy lệnh '{str(fnf_error)}' để chạy .{file_extension}.", "output": "", "error": f"FileNotFoundError: {str(fnf_error)}", "return_code": -1, "warning": admin_warning, "codeThatFailed": code_to_execute}), 500
    except Exception as e:
        traceback.print_exc(file=sys.stderr)
        return jsonify({"error": f"Lỗi hệ thống khi thực thi: {e}", "output": "", "error": str(e), "return_code": -1, "warning": admin_warning, "codeThatFailed": code_to_execute}), 500
    finally:
        if temp_file_path and os.path.exists(temp_file_path):
            try: os.remove(temp_file_path)
            except Exception as cleanup_e: print(f"[LỖI] Ko xóa đc file tạm {temp_file_path}: {cleanup_e}")


# Endpoint để gỡ lỗi code
@app.route('/api/debug', methods=['POST'])
def handle_debug():
    data = request.get_json()
    original_prompt = data.get('prompt', '(Không có prompt gốc)')
    failed_code = data.get('code')
    stdout = data.get('stdout', '')
    stderr = data.get('stderr', '')
    model_config = data.get('model_config', {})
    file_type = data.get('file_type', 'py') # Loại file gây lỗi

    if not failed_code:
        return jsonify({"error": "Thiếu mã lỗi để gỡ rối."}), 400

    language_extension = file_type.split('.')[-1].lower() if '.' in file_type else file_type.lower()
    if not language_extension: language_extension = 'py'

    full_prompt = create_debug_prompt(original_prompt, failed_code, stdout, stderr, language_extension)
    raw_response = generate_response_from_gemini(full_prompt, model_config.copy(), is_for_review_or_debug=True)

    if raw_response and not raw_response.startswith("Lỗi"):
        explanation_part = raw_response
        corrected_code = None
        suggested_package = None

        if language_extension == 'py':
            install_match = re.search(r"```bash\s*pip install\s+([\w\-==\.]+)\s*```", explanation_part, re.IGNORECASE)
            if install_match:
                suggested_package = install_match.group(1).strip()
                explanation_part = explanation_part[:install_match.start()].strip() + explanation_part[install_match.end():].strip()

        last_code_block_match = None
        code_block_tag = language_extension if language_extension.isalnum() else 'code'
        if language_extension == "fortigatecli": code_block_tag = "fortigatecli" # Cho debug FortiGate

        patterns_to_try = [r"```" + re.escape(code_block_tag) + r"\s*([\s\S]*?)\s*```"]
        if language_extension == 'py': patterns_to_try.append(r"```python\s*([\s\S]*?)\s*```")
        # ... them alias khac neu can ...
        patterns_to_try.append(r"```\s*([\s\S]*?)\s*```")

        for pattern in patterns_to_try:
             matches = list(re.finditer(pattern, explanation_part, re.IGNORECASE | re.MULTILINE))
             if matches: last_code_block_match = matches[-1]; break

        if last_code_block_match:
            potential_explanation = explanation_part[:last_code_block_match.start()].strip()
            explanation_part = potential_explanation if potential_explanation else f"(AI chỉ trả về code {get_language_name(language_extension)} đã sửa, không có giải thích)"
            corrected_code = last_code_block_match.group(1).strip()

        explanation_part = re.sub(r"^(Phân tích và đề xuất:|Giải thích và đề xuất初期設定:|Phân tích:|Giải thích:)\s*", "", explanation_part, flags=re.IGNORECASE | re.MULTILINE).strip()
        return jsonify({"explanation": explanation_part or "(Không có giải thích)", "corrected_code": corrected_code, "suggested_package": suggested_package, "original_language": language_extension})
    elif raw_response:
        return jsonify({"error": raw_response}), 400 if "Lỗi cấu hình" in raw_response else 500
    else:
        return jsonify({"error": "Không thể thực hiện gỡ rối."}), 500


# Endpoint để cài đặt package Python bằng pip
@app.route('/api/install_package', methods=['POST'])
def handle_install_package():
    data = request.get_json()
    package_name = data.get('package_name')

    if not package_name: return jsonify({"error": "Thiếu tên package."}), 400
    if not re.fullmatch(r"^[a-zA-Z0-9\-_==\.\+\[\]]+$", package_name): # Cho phep extras
        return jsonify({"success": False, "error": f"Tên package không hợp lệ: {package_name}"}), 400

    try:
        command = [sys.executable, '-m', 'pip', 'install'] + shlex.split(package_name)
        command = [part for part in command if part] # Loai bo phan tu rong
    except Exception as parse_err:
        return jsonify({"success": False, "error": f"Tên package không hợp lệ: {package_name} ({parse_err})"}), 400

    try:
        process_env = os.environ.copy(); process_env["PYTHONIOENCODING"] = "utf-8"
        result = subprocess.run(command, capture_output=True, encoding='utf-8', errors='replace', timeout=120, check=False, env=process_env, text=True)
        if result.returncode == 0:
            return jsonify({ "success": True, "message": f"Cài đặt '{package_name}' thành công.", "output": result.stdout, "error": result.stderr })
        else:
            detailed_error = result.stderr.strip().split('\n')[-1] if result.stderr.strip() else f"Pip thất bại với mã {result.returncode}."
            return jsonify({ "success": False, "message": f"Cài đặt '{package_name}' thất bại.", "output": result.stdout, "error": detailed_error }), 500
    except subprocess.TimeoutExpired:
        return jsonify({"success": False, "error": f"Timeout khi cài đặt '{package_name}'.", "output": "", "error_detail": "Timeout"}), 408
    except FileNotFoundError:
         return jsonify({"success": False, "error": "Lỗi: Không tìm thấy Python hoặc Pip.", "output": "", "error_detail": "FileNotFoundError"}), 500
    except Exception as e:
        traceback.print_exc(file=sys.stderr)
        return jsonify({"success": False, "error": f"Lỗi hệ thống khi cài đặt: {e}", "output": "", "error_detail": str(e)}), 500


# Endpoint để giải thích nội dung
@app.route('/api/explain', methods=['POST'])
def handle_explain():
    data = request.get_json()
    content_to_explain = data.get('content')
    context = data.get('context', 'unknown')
    model_config = data.get('model_config', {})
    file_type = data.get('file_type')

    if not content_to_explain:
        return jsonify({"error": "Không có nội dung để giải thích."}), 400

    if isinstance(content_to_explain, (dict, list)):
         try: content_to_explain = json.dumps(content_to_explain, ensure_ascii=False, indent=2)
         except: content_to_explain = str(content_to_explain)
    else: content_to_explain = str(content_to_explain)

    explain_context = 'code' if context == 'python_code' else context # context 'code' chung
    language_for_prompt = file_type if explain_context == 'code' else None # gui language neu la code

    full_prompt = create_explain_prompt(content_to_explain, explain_context, language=language_for_prompt)
    explanation_text = generate_response_from_gemini(full_prompt, model_config.copy(), is_for_review_or_debug=True)

    if explanation_text and not explanation_text.startswith("Lỗi"):
        return jsonify({"explanation": explanation_text})
    elif explanation_text:
        return jsonify({"error": explanation_text}), 400 if "Lỗi cấu hình" in explanation_text else 500
    else:
        return jsonify({"error": "Không thể tạo giải thích."}), 500


if __name__ == '__main__':
    print("Backend đang chạy tại http://localhost:5001")
    if sys.platform == "win32":
        try:
            is_admin = ctypes.windll.shell32.IsUserAnAdmin() != 0
            print(f"[INFO] Backend chạy với quyền: {'Administrator' if is_admin else 'User thông thường'}.")
        except: print("[CẢNH BÁO] Không thể kiểm tra quyền admin.")
    app.run(debug=True, port=5001)