import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "RAG Backborn",
  description: "계층형 파일 저장소 위에서 동작하는 RAG 챗봇 백본",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="ko">
      <body>{children}</body>
    </html>
  );
}
