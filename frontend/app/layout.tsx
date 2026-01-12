import type { Metadata } from 'next';
import { Noto_Sans_KR } from 'next/font/google';
import './globals.css';

// 브런치 스타일: Noto Sans KR (DemiLight = 300)
const notoSans = Noto_Sans_KR({
  weight: ['300', '400', '500', '700'],
  subsets: ['latin'],
  variable: '--font-sans',
  display: 'swap',
});

export const metadata: Metadata = {
  title: 'Essay Garden',
  description: '노션 메모에서 피어나는 글감',
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="ko" className={notoSans.variable}>
      <body className="font-sans bg-brunch-bg text-brunch-text antialiased">
        {children}
      </body>
    </html>
  );
}
