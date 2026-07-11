import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { App as AntApp, ConfigProvider } from "antd";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import "antd/dist/reset.css";
import "./index.css";
import App from "./App";

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      refetchOnWindowFocus: false,
      retry: 1,
    },
  },
});

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <ConfigProvider
        theme={{
          token: {
            // 统一品牌主色 = indigo-600;让 AntD 组件与自定义样式(链接/激活态/主按钮)同源
            colorPrimary: "#4f46e5",
            colorInfo: "#4f46e5",
            colorLink: "#4f46e5",
            colorLinkHover: "#4338ca",
            // 文本/边框/背景对齐 slate 体系,避免 AntD 默认蓝灰与页面 slate 不一致
            colorText: "#0f172a",
            colorTextSecondary: "#475569",
            colorBorderSecondary: "#e2e8f0",
            colorBgLayout: "#f8fafc",
            // 双层圆角:控件 6(rounded-md)/ 卡片弹窗 8(rounded-lg),与 index.css 一致
            borderRadius: 6,
            borderRadiusLG: 8,
            fontSize: 14,
            controlHeight: 36,
            fontFamily: "IBM Plex Sans, ui-sans-serif, system-ui, sans-serif",
          },
          components: {
            Table: { headerBg: "#f8fafc", headerColor: "#475569", borderColor: "#e2e8f0" },
            Button: { primaryShadow: "none", defaultShadow: "none" },
          },
        }}
      >
        <AntApp>
          <App />
        </AntApp>
      </ConfigProvider>
    </QueryClientProvider>
  </StrictMode>
);
