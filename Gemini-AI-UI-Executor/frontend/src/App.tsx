// frontend/src/App.tsx
import React, { useState, ChangeEvent, useEffect, useCallback } from 'react';
import CenterArea from './components/CenterArea';
import Sidebar from './components/Sidebar';
import { ToastContainer, toast } from 'react-toastify';
import 'react-toastify/dist/ReactToastify.css';
import './App.css';

// --- Định nghĩa kiểu dữ liệu (Interfaces) ---
export interface ExecutionResult {
  message: string;
  output: string;
  error: string;
  return_code: number;
  codeThatFailed?: string;
  warning?: string;
  executed_file_type?: string;
}

export interface ReviewResult {
    review?: string;
    error?: string;
}

export interface ModelConfig {
    modelName: string;
    temperature: number;
    topP: number;
    topK: number;
    safetySetting: string;
    api_key?: string;
}

export interface DebugResult {
    explanation: string | null;
    corrected_code: string | null;
    suggested_package?: string;
    error?: string;
    original_language?: string;
}

export interface ExplainResult {
    explanation?: string;
    error?: string;
}

export interface InstallationResult {
    success: boolean;
    message: string;
    output: string;
    error: string;
    package_name: string;
}

export type TargetOS = 'auto' | 'windows' | 'linux' | 'macos';

export interface FortiGateConfig {
  ipHost: string;
  portSsh: string;
  username: string;
  password?: string;
}

export interface ConversationBlock {
    type: 'user' | 'ai-code' | 'review' | 'execution' | 'debug' | 'loading' | 'error' | 'installation' | 'explanation' | 'placeholder';
    data: any;
    id: string;
    timestamp: string;
    isNew?: boolean;
    generatedType?: string; // .py, .bat, .sh, fortigatecli
}
// ---------------------------------------------

const MODEL_NAME_STORAGE_KEY = 'geminiExecutorModelName';
const FORTIGATE_CONFIG_STORAGE_KEY = 'geminiExecutorFortiGateConfig';
const NEW_BLOCK_ANIMATION_DURATION = 500;

function App() {
  const [prompt, setPrompt] = useState<string>('');
  const [conversation, setConversation] = useState<Array<ConversationBlock>>([]);
  const [isLoading, setIsLoading] = useState<boolean>(false);
  const [isExecuting, setIsExecuting] = useState<boolean>(false); // Chung cho file & FGT
  const [isReviewing, setIsReviewing] = useState<boolean>(false);
  const [isDebugging, setIsDebugging] = useState<boolean>(false);
  const [isInstalling, setIsInstalling] = useState<boolean>(false);
  const [isExplaining, setIsExplaining] = useState<boolean>(false);

  const [modelConfig, setModelConfig] = useState<ModelConfig>({
    modelName: localStorage.getItem(MODEL_NAME_STORAGE_KEY) || 'gemini-1.5-flash',
    temperature: 0.7, topP: 0.95, topK: 40, safetySetting: 'BLOCK_MEDIUM_AND_ABOVE',
  });

  const [fortiGateConfig, setFortiGateConfig] = useState<FortiGateConfig>(() => {
    const saved = localStorage.getItem(FORTIGATE_CONFIG_STORAGE_KEY);
    return saved ? JSON.parse(saved) : { ipHost: '', portSsh: '22', username: 'admin', password: '' };
  });

  const [collapsedStates, setCollapsedStates] = useState<Record<string, boolean>>({});
  const [expandedOutputs, setExpandedOutputs] = useState<Record<string, { stdout: boolean; stderr: boolean }>>({});
  const [isSidebarOpen, setIsSidebarOpen] = useState<boolean>(false);
  const [runAsAdmin, setRunAsAdmin] = useState<boolean>(false);
  const [uiApiKey, setUiApiKey] = useState<string>('');
  const [useUiApiKey, setUseUiApiKey] = useState<boolean>(false);
  const [targetOs, setTargetOs] = useState<TargetOS>('auto');
  const [fileType, setFileType] = useState<string>('py');
  const [customFileName, setCustomFileName] = useState<string>('');

   useEffect(() => { // Xoa isNew
        const timers: NodeJS.Timeout[] = [];
        conversation.filter(b => b.isNew).forEach(block => {
            const timer = setTimeout(() => {
                setConversation(prev => prev.map(b => (b.id === block.id ? { ...b, isNew: false } : b)));
            }, NEW_BLOCK_ANIMATION_DURATION + 100);
            timers.push(timer);
        });
        return () => timers.forEach(clearTimeout);
   // eslint-disable-next-line react-hooks/exhaustive-deps
   }, [conversation.filter(b => b.isNew).map(b => b.id).join(',')]);


  const handleConfigChange = useCallback((e: ChangeEvent<HTMLInputElement | HTMLSelectElement>) => {
    const { name, value, type } = e.target;
    const checked = (e.target as HTMLInputElement).checked; // for checkbox

    if (name === 'targetOs') {
      setTargetOs(value as TargetOS);
      if (fileType !== 'other') setFileType(value === 'windows' ? 'bat' : (value === 'auto' ? 'py' : 'sh'));
    } else if (name === 'fileType') {
      setFileType(value);
      if (value !== 'other') setCustomFileName('');
    } else if (name === 'customFileName') {
      setCustomFileName(value);
    } else if (['modelName', 'temperature', 'topP', 'topK', 'safetySetting'].includes(name)) {
        setModelConfig(prev => ({ ...prev, [name]: (type === 'number' || name === 'temperature' || name === 'topP' || name === 'topK') ? parseFloat(value) : value }));
    } else if (name === 'runAsAdmin' && type === 'checkbox') {
        setRunAsAdmin(checked);
    } else if (name === 'uiApiKey' && (type === 'password' || type === 'text')) {
        setUiApiKey(value);
    } else if (['ipHost', 'portSsh', 'username', 'password'].includes(name) && Object.keys(fortiGateConfig).includes(name)) {
        setFortiGateConfig(prev => {
            const newFgtCfg = { ...prev, [name]: value };
            localStorage.setItem(FORTIGATE_CONFIG_STORAGE_KEY, JSON.stringify(newFgtCfg));
            return newFgtCfg;
        });
    }
  }, [fileType, fortiGateConfig]);

  const handleSaveSettings = useCallback(() => { // Chi luu model name
    localStorage.setItem(MODEL_NAME_STORAGE_KEY, modelConfig.modelName);
    toast.success(`Đã lưu model: ${modelConfig.modelName}`);
  }, [modelConfig.modelName]);

  const handleApplyUiApiKey = useCallback(() => { uiApiKey.trim() ? (setUseUiApiKey(true), toast.info("Dùng API Key từ Cài đặt.")) : toast.warn("Nhập API Key trước."); }, [uiApiKey]);
  const handleUseEnvKey = useCallback(() => { setUseUiApiKey(false); toast.info("Dùng API Key từ .env (nếu có)."); }, []);
  const handleToggleSidebar = useCallback(() => { setIsSidebarOpen(prev => !prev); }, []);
  const toggleCollapse = useCallback((blockId: string) => setCollapsedStates(prev => ({ ...prev, [blockId]: !prev[blockId] })), []);
  const onToggleOutputExpand = useCallback((blockId: string, type: 'stdout' | 'stderr') => setExpandedOutputs(prev => ({ ...prev, [blockId]: { ...(prev[blockId] || { stdout: false, stderr: false }), [type]: !(prev[blockId]?.[type] ?? false) }})), []);

  const sendApiRequest = useCallback(async (endpoint: string, body: object, isFortiGateRequest: boolean = false) => {
    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), 90000); // Tang timeout cho FGT

    let effectiveModelConfig = { ...modelConfig };
    const needsApiKey = ['generate', 'review', 'debug', 'explain', 'fortigate_generate_script'].includes(endpoint); // Them fgt endpoint
    if (useUiApiKey && uiApiKey && needsApiKey) effectiveModelConfig = { ...effectiveModelConfig, api_key: uiApiKey };
    else delete effectiveModelConfig.api_key;

    let finalBody = { ...body, model_config: effectiveModelConfig };

    if (isFortiGateRequest) {
        if (!fortiGateConfig.ipHost || !fortiGateConfig.username) {
            toast.error("Vui lòng nhập đủ thông tin kết nối FortiGate trong Cài đặt.");
            setIsSidebarOpen(true);
            clearTimeout(timeoutId);
            throw new Error("Thiếu cấu hình FortiGate");
        }
        finalBody = { ...finalBody, fortigate_config: fortiGateConfig };
    } else { // Chi gui target_os, file_type cho script thuong
        const finalFileType = fileType === 'other' ? customFileName.trim() || 'txt' : fileType;
        finalBody = { ...finalBody, target_os: targetOs, file_type: finalFileType };
    }


    try {
        const response = await fetch(`http://localhost:5001/api/${endpoint}`, {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(finalBody), signal: controller.signal
        });
        clearTimeout(timeoutId);
        const data = await response.json();
        if (!response.ok) throw new Error(data?.error || `Lỗi ${response.status} từ /${endpoint}`);
        return data;
    } catch (error: any) {
         clearTimeout(timeoutId);
         if (error.name === 'AbortError') throw new Error(`Yêu cầu /${endpoint} quá thời gian.`);
         throw error; // nem loi de ham goi xu ly
    }
  }, [modelConfig, useUiApiKey, uiApiKey, targetOs, fileType, customFileName, fortiGateConfig]);


   const handleGenerate = useCallback(async (currentPrompt: string) => {
        if (!currentPrompt.trim()) { toast.warn('Nhập yêu cầu.'); return; }
        setIsLoading(true);
        const now = new Date().toISOString();
        const newCollapsedStates: Record<string, boolean> = {};
        conversation.filter(b => b.type === 'user').forEach(block => { newCollapsedStates[block.id] = true; });
        setCollapsedStates(prev => ({ ...prev, ...newCollapsedStates }));

        const newUserBlock: ConversationBlock = { type: 'user', data: currentPrompt, id: Date.now().toString() + '_u', timestamp: now, isNew: true };
        const loadingId = Date.now().toString() + '_gload';
        const loadingBlock: ConversationBlock = { type: 'loading', data: 'Đang tạo...', id: loadingId, timestamp: now, isNew: true };

        setConversation(prev => [...prev, newUserBlock, loadingBlock]);
        setCollapsedStates(prev => ({ ...prev, [newUserBlock.id]: false })); // Mo rong round moi

        // Xac dinh endpoint va isFortiGateRequest
        const isFGTRequest = currentPrompt.toLowerCase().includes("fortigate") || currentPrompt.toLowerCase().includes("fgt");
        const endpoint = isFGTRequest ? 'fortigate_generate_script' : 'generate';

        try {
          const data = await sendApiRequest(endpoint, { prompt: currentPrompt }, isFGTRequest);
          setConversation(prev => prev.map(b =>
                b.id === loadingId
                ? { type: 'ai-code', data: data.code, generatedType: data.generated_for_type, id: Date.now().toString() + '_a', timestamp: new Date().toISOString(), isNew: true }
                : b
            ));
          toast.success(isFGTRequest ? "Đã tạo lệnh FortiGate!" : "Đã tạo mã!");
          setPrompt('');
        } catch (err: any) {
          const errorMessage = err.message || (isFGTRequest ? 'Lỗi tạo lệnh FGT.' : 'Lỗi tạo mã.');
          toast.error(errorMessage);
          setConversation(prev => prev.map(b => b.id === loadingId ? { type: 'error', data: errorMessage, id: Date.now().toString() + '_err', timestamp: new Date().toISOString(), isNew: true } : b));
        } finally { setIsLoading(false); }
    }, [sendApiRequest, conversation, setPrompt]);

    const handleReviewCode = useCallback(async (codeToReview: string | null, blockId: string) => {
        if (!codeToReview) { toast.warn("Ko có mã/lệnh để review."); return; }
        const blockToReview = conversation.find(b => b.id === blockId);
        const fileTypeToSend = blockToReview?.generatedType || 'py';

        setIsReviewing(true);
        const now = new Date().toISOString();
        const loadingId = Date.now().toString() + '_rload';
        const loadingBlock: ConversationBlock = { type: 'loading', data: 'Đang đánh giá...', id: loadingId, timestamp: now, isNew: true };
        const originalBlockIndex = conversation.findIndex(b => b.id === blockId);
        const newConv = [...conversation];
        if (originalBlockIndex !== -1) newConv.splice(originalBlockIndex + 1, 0, loadingBlock); else newConv.push(loadingBlock);
        setConversation(newConv);

        try {
            const data: ReviewResult = await sendApiRequest('review', { code: codeToReview, file_type: fileTypeToSend });
            setConversation(prev => prev.map(b => b.id === loadingId ? { type: 'review', data, id: Date.now().toString() + '_r', timestamp: new Date().toISOString(), isNew: true } : b));
            toast.success("Đã đánh giá xong!");
        } catch (err: any) {
            const errorData: ReviewResult = { error: err.message || 'Lỗi review.' };
            setConversation(prev => prev.map(b => b.id === loadingId ? { type: 'review', data: errorData, id: Date.now().toString() + '_rerr', timestamp: new Date().toISOString(), isNew: true } : b));
            toast.error(err.message || 'Lỗi review.');
        } finally { setIsReviewing(false); }
    }, [sendApiRequest, conversation]);

    // Chung cho file script va FortiGate CLI
    const handleExecute = useCallback(async (codeToExecute: string | null, blockId: string) => {
        if (!codeToExecute) { toast.warn("Ko có mã/lệnh để thực thi."); return; }
        const blockToExecute = conversation.find(b => b.id === blockId);
        const execType = blockToExecute?.generatedType || 'py'; // VD: py, sh, bat, fortigatecli

        setIsExecuting(true);
        const toastMsg = execType === 'fortigatecli' ? 'Đang gửi lệnh tới FortiGate...' : `Đang thực thi ${runAsAdmin ? ' với quyền Admin/Root' : ''}...`;
        const toastId = toast.loading(toastMsg);
        const executionBlockId = Date.now().toString() + '_ex';
        const now = new Date().toISOString();
        const executionBlockBase: Partial<ConversationBlock> = { type: 'execution', id: executionBlockId, timestamp: now, isNew: true };
        const originalBlockIndex = conversation.findIndex(b => b.id === blockId);
        const newConv = [...conversation];
        let resultData: ExecutionResult | null = null;

        const endpoint = execType === 'fortigatecli' ? 'fortigate_execute_script' : 'execute';
        const payload = execType === 'fortigatecli'
            ? { code: codeToExecute } // fortigate_config se duoc them boi sendApiRequest
            : { code: codeToExecute, run_as_admin: runAsAdmin, file_type: execType };

        try {
            const data: ExecutionResult = await sendApiRequest(endpoint, payload, execType === 'fortigatecli');
            resultData = { ...data, codeThatFailed: codeToExecute, executed_file_type: execType }; // Luu lai type

            if (data.warning) toast.warning(data.warning, { autoClose: 7000, toastId: `warning-${executionBlockId}` });
            const stdoutErrorKeywords = ['lỗi', 'error', 'fail', 'cannot', 'unable', 'traceback', 'exception', 'not found', 'không tìm thấy', 'invalid', 'command parse error'];
            const stdoutLooksLikeError = data.output?.trim() && stdoutErrorKeywords.some(kw => data.output.toLowerCase().includes(kw));
            const hasError = data.return_code !== 0 || !!data.error?.trim() || data.return_code === -200;

            if (!hasError && !stdoutLooksLikeError) toast.update(toastId, { render: "Thực thi thành công!", type: "success", isLoading: false, autoClose: 3000 });
            else if (!hasError && stdoutLooksLikeError) toast.update(toastId, { render: "Đã thực thi, output có thể chứa vấn đề.", type: "warning", isLoading: false, autoClose: 5000 });
            else toast.update(toastId, { render: data.error === 'Timeout' ? "Thực thi quá tgian." : (data.error || "Thực thi thất bại/có lỗi."), type: "error", isLoading: false, autoClose: 5000 });

        } catch (err: any) {
             resultData = { message: err.message || "Lỗi thực thi.", output: "", error: err.message || "Lỗi không xác định", return_code: -200, codeThatFailed: codeToExecute, executed_file_type: execType };
            toast.update(toastId, { render: `Lỗi thực thi: ${resultData.error}`, type: "error", isLoading: false, autoClose: 5000 });
        } finally {
            setIsExecuting(false);
            if (resultData) {
                 const blockToAdd = { ...executionBlockBase, data: resultData } as ConversationBlock;
                 if (originalBlockIndex !== -1) { newConv.splice(originalBlockIndex + 1, 0, blockToAdd); setConversation(newConv); }
                 else setConversation(prev => [...prev, blockToAdd]);
            }
        }
      }, [runAsAdmin, conversation, sendApiRequest]);


      const handleDebug = useCallback(async (codeToDebug: string | null, lastExecutionResult: ExecutionResult | null, blockId: string) => {
           const hasErrorSignal = (execResult: ExecutionResult | null): boolean => {
               if (!execResult) return false;
               const keywords = ['lỗi', 'error', 'fail', 'cannot', 'unable', 'traceback', 'exception', 'not found', 'không tìm thấy', 'invalid', 'command parse error'];
               return execResult.return_code !== 0 || !!execResult.error?.trim() || execResult.return_code === -200 || (!!execResult.output?.trim() && keywords.some(kw => execResult.output!.toLowerCase().includes(kw)));
           };
           if (!codeToDebug || !hasErrorSignal(lastExecutionResult)) { toast.warn("Cần mã/lệnh và kết quả lỗi để gỡ rối."); return; }

           const fileTypeToSend = lastExecutionResult?.executed_file_type || 'py'; // VD: py, fortigatecli

           setIsDebugging(true);
           const now = new Date().toISOString();
           const loadingId = Date.now().toString() + '_dload';
           const loadingBlock: ConversationBlock = { type: 'loading', data: 'Đang gỡ rối...', id: loadingId, timestamp: now, isNew: true };
           const originalBlockIndex = conversation.findIndex(b => b.id === blockId);
           const newConv = [...conversation];
           if (originalBlockIndex !== -1) newConv.splice(originalBlockIndex + 1, 0, loadingBlock); else newConv.push(loadingBlock);
           setConversation(newConv);

           let userPromptForDebug = "(Ko tìm thấy prompt gốc)";
           let foundExec = false;
           for (const block of [...conversation].reverse()) {
                if (!foundExec && block.id === blockId && block.type === 'execution') foundExec = true;
                if (foundExec && block.type === 'user') { userPromptForDebug = block.data; break; }
           }

           try {
               const data: DebugResult = await sendApiRequest('debug', {
                   prompt: userPromptForDebug, code: codeToDebug,
                   stdout: lastExecutionResult?.output ?? '', stderr: lastExecutionResult?.error ?? '',
                   file_type: fileTypeToSend // Gui type cho backend
               });
               setConversation(prev => prev.map(b => b.id === loadingId ? { type: 'debug', data, id: Date.now().toString() + '_dbg', timestamp: new Date().toISOString(), isNew: true } : b));
               toast.success("Đã phân tích gỡ rối!");
           } catch (err: any) {
               const errorData: DebugResult = { explanation: null, corrected_code: null, error: err.message };
               setConversation(prev => prev.map(b => b.id === loadingId ? { type: 'debug', data: errorData, id: Date.now().toString() + '_dbgerr', timestamp: new Date().toISOString(), isNew: true } : b));
               toast.error(`Gỡ rối thất bại: ${err.message}`);
           } finally { setIsDebugging(false); }
       }, [conversation, sendApiRequest]);

       const applyCorrectedCode = useCallback((correctedCode: string, originalDebugBlockId: string) => {
           const debugBlock = conversation.find(b => b.id === originalDebugBlockId);
           const newGeneratedType = debugBlock?.data?.original_language || 'py'; // Lay lai type goc

           const newBlock: ConversationBlock = {
               type: 'ai-code', data: correctedCode, generatedType: newGeneratedType,
               id: Date.now().toString() + '_ac', timestamp: new Date().toISOString(), isNew: true
           };
           const originalBlockIndex = conversation.findIndex(b => b.id === originalDebugBlockId);
           const newConv = [...conversation];
           if (originalBlockIndex !== -1) newConv.splice(originalBlockIndex + 1, 0, newBlock); else newConv.push(newBlock);
           setConversation(newConv);
           toast.success("Đã áp dụng code sửa lỗi.");
       }, [conversation]);

     const handleInstallPackage = useCallback(async (packageName: string, originalDebugBlockId: string) => {
         if (!packageName) { toast.warn("Ko có tên package."); return; }
         setIsInstalling(true);
         const toastId = toast.loading(`Đang cài ${packageName}...`);
         const installBlockId = Date.now().toString() + '_inst';
         const now = new Date().toISOString();
         const installBlockBase: Partial<ConversationBlock> = { type: 'installation', id: installBlockId, timestamp: now, isNew: true };
         const originalBlockIndex = conversation.findIndex(b => b.id === originalDebugBlockId);
         const newConv = [...conversation];
         let resultData : InstallationResult | null = null;

         try { // Ko dung sendApiRequest vi endpoint nay ko can model_config
             const response = await fetch('http://localhost:5001/api/install_package', {
                 method: 'POST', headers: { 'Content-Type': 'application/json' },
                 body: JSON.stringify({ package_name: packageName }),
             });
             const data: InstallationResult = await response.json();
             resultData = { ...data, package_name: packageName };
             if (data.success) toast.update(toastId, { render: `Cài ${packageName} thành công!`, type: "success", isLoading: false, autoClose: 4000 });
             else { const errMsg = data.error || data.output || "Cài đặt thất bại."; toast.update(toastId, { render: `Cài ${packageName} thất bại. ${errMsg.split('\n')[0]}`, type: "error", isLoading: false, autoClose: 6000 }); }
         } catch (err: any) {
             resultData = { success: false, message: `Lỗi kết nối khi cài.`, output: "", error: err.message, package_name: packageName };
             toast.update(toastId, { render: `Lỗi cài đặt: ${err.message}`, type: "error", isLoading: false, autoClose: 5000 });
         } finally {
             setIsInstalling(false);
              if (resultData) {
                  const blockToAdd = { ...installBlockBase, data: resultData } as ConversationBlock;
                  if (originalBlockIndex !== -1) { newConv.splice(originalBlockIndex + 1, 0, blockToAdd); setConversation(newConv); }
                  else setConversation(prev => [...prev, blockToAdd]);
              }
         }
     }, [conversation]);

    const handleExplain = useCallback(async (blockId: string, contentToExplain: any, context: string) => {
        const blockToExplain = conversation.find(b => b.id === blockId);
        const fileTypeToSend = (context === 'code') ? blockToExplain?.generatedType : undefined; // VD: py, fortigatecli

        setIsExplaining(true);
        const now = new Date().toISOString();
        const loadingId = Date.now().toString() + '_explload';
        const loadingBlock: ConversationBlock = { type: 'loading', data: `Đang giải thích...`, id: loadingId, timestamp: now, isNew: true };
        const originalBlockIndex = conversation.findIndex(b => b.id === blockId);
        const newConv = [...conversation];
        if (originalBlockIndex !== -1) newConv.splice(originalBlockIndex + 1, 0, loadingBlock); else newConv.push(loadingBlock);
        setConversation(newConv);

        let processedContent = contentToExplain;
        if (typeof contentToExplain === 'object' && contentToExplain !== null) {
            try {
                let contentToSend = { ...contentToExplain }; // Sao chep de ko sua doi state goc
                // Loai bo field ko can thiet
                if ((context === 'execution_result' || context === 'debug_result') && 'codeThatFailed' in contentToSend) delete contentToSend.codeThatFailed;
                processedContent = JSON.stringify(contentToSend, null, 2);
            } catch { processedContent = String(contentToExplain); }
        } else processedContent = String(contentToExplain);

        try {
            const data: ExplainResult = await sendApiRequest('explain', {
                content: processedContent, context,
                ...(fileTypeToSend && { file_type: fileTypeToSend }) // Them neu co
            });
            setConversation(prev => prev.map(b => b.id === loadingId ? { type: 'explanation', data, id: Date.now().toString() + '_exp', timestamp: new Date().toISOString(), isNew: true } : b));
            toast.success("Đã tạo giải thích!");
        } catch (err: any) {
            const errorData: ExplainResult = { error: err.message || 'Lỗi giải thích.' };
            setConversation(prev => prev.map(b => b.id === loadingId ? { type: 'explanation', data: errorData, id: Date.now().toString() + '_experr', timestamp: new Date().toISOString(), isNew: true } : b));
            toast.error(err.message || 'Lỗi giải thích.');
        } finally { setIsExplaining(false); }
      }, [sendApiRequest, conversation]);

  const isBusy = isLoading || isExecuting || isReviewing || isDebugging || isInstalling || isExplaining;

  return (
    <div className="main-container">
      <ToastContainer theme="dark" position="bottom-right" autoClose={4000} hideProgressBar={false} newestOnTop={false} closeOnClick rtl={false} pauseOnFocusLoss draggable pauseOnHover />
      <CenterArea
        conversation={conversation}
        isLoading={isLoading}
        isBusy={isBusy}
        prompt={prompt}
        setPrompt={setPrompt}
        onGenerate={handleGenerate}
        onReview={handleReviewCode}
        onExecute={handleExecute} // Ham thuc thi chung
        onDebug={handleDebug}
        onApplyCorrectedCode={applyCorrectedCode}
        onInstallPackage={handleInstallPackage}
        onExplain={handleExplain}
        collapsedStates={collapsedStates}
        onToggleCollapse={toggleCollapse}
        expandedOutputs={expandedOutputs}
        onToggleOutputExpand={onToggleOutputExpand}
        onToggleSidebar={handleToggleSidebar}
      />
      <Sidebar
        isOpen={isSidebarOpen}
        onClose={() => setIsSidebarOpen(false)}
        modelConfig={modelConfig}
        onConfigChange={handleConfigChange}
        onSaveSettings={handleSaveSettings}
        isBusy={isBusy}
        runAsAdmin={runAsAdmin}
        uiApiKey={uiApiKey}
        useUiApiKey={useUiApiKey}
        onApplyUiApiKey={handleApplyUiApiKey}
        onUseEnvKey={handleUseEnvKey}
        targetOs={targetOs}
        fileType={fileType}
        customFileName={customFileName}
        fortiGateConfig={fortiGateConfig} // Pass FGT config
      />
    </div>
  );
}
export default App;