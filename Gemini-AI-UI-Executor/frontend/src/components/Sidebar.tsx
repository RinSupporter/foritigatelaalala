// frontend/src/components/Sidebar.tsx
import React, { ChangeEvent } from 'react';
import { FiX } from 'react-icons/fi';
import SettingsPanel from './SettingsPanel';
import { ModelConfig, TargetOS, FortiGateConfig } from '../App'; // Them FortiGateConfig
import './Sidebar.css';

interface SidebarProps {
  isOpen: boolean;
  onClose: () => void;
  modelConfig: ModelConfig;
  onConfigChange: (e: ChangeEvent<HTMLInputElement | HTMLSelectElement>) => void;
  onSaveSettings: () => void;
  isBusy: boolean;
  runAsAdmin: boolean;
  uiApiKey: string;
  useUiApiKey: boolean;
  onApplyUiApiKey: () => void;
  onUseEnvKey: () => void;
  targetOs: TargetOS;
  fileType: string;
  customFileName: string;
  fortiGateConfig: FortiGateConfig; // Them prop nay
}

const Sidebar: React.FC<SidebarProps> = ({
  isOpen, onClose, modelConfig, onConfigChange, onSaveSettings, isBusy,
  runAsAdmin, uiApiKey, useUiApiKey, onApplyUiApiKey, onUseEnvKey,
  targetOs, fileType, customFileName,
  fortiGateConfig, // Nhan prop nay
}) => {

  return (
    <>
      <div className={`sidebar-overlay ${isOpen ? 'visible' : ''}`} onClick={onClose} aria-hidden={!isOpen}></div>
      <aside className={`sidebar-container ${isOpen ? 'open' : ''}`} aria-label="Sidebar Settings">
        <div className="sidebar-header">
          <h3>ᓚᘏᗢ | Run Setting</h3>
          <button onClick={onClose} className="icon-button subtle close-sidebar-button" title="Đóng" aria-label="Đóng"><FiX /></button>
        </div>
        <div className="sidebar-content">
          <SettingsPanel
            modelConfig={modelConfig}
            onConfigChange={onConfigChange}
            onSaveSettings={onSaveSettings}
            isDisabled={isBusy}
            runAsAdmin={runAsAdmin}
            uiApiKey={uiApiKey}
            useUiApiKey={useUiApiKey}
            onApplyUiApiKey={onApplyUiApiKey}
            onUseEnvKey={onUseEnvKey}
            targetOs={targetOs}
            fileType={fileType}
            customFileName={customFileName}
            fortiGateConfig={fortiGateConfig} // Truyen xuong
          />
        </div>
      </aside>
    </>
  );
};
export default Sidebar;