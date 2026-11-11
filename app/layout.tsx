import "./globals.css";

export const metadata = {
  title: "训推平台",
  icons: { icon: "/logo.png" },
  description: "企业内部模型训练与推理平台"
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="zh-CN">
      <body>{children}</body>
    </html>
  );
}
