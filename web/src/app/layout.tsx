import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "HRAgents · 企业 HR 可信智能体平台",
  description: "面向企业 HR 高可信知识与流程场景的可控、可验证、可迭代智能体平台",
};

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="zh-CN">
      <body>{children}</body>
    </html>
  );
}
