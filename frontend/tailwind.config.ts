import type { Config } from "tailwindcss";

const config: Config = {
  content: [
    "./pages/**/*.{js,ts,jsx,tsx,mdx}",
    "./components/**/*.{js,ts,jsx,tsx,mdx}",
    "./app/**/*.{js,ts,jsx,tsx,mdx}",
  ],
  theme: {
    extend: {
      colors: {
        brunch: {
          bg: '#ffffff',        // 실제 브런치 배경색
          text: '#333333',      // 실제 브런치 주 텍스트
          textLight: '#999999', // 실제 브런치 보조 텍스트 (메타정보)
          textMedium: '#6c6c6c', // 중간 회색
          border: '#e5e5e5',    // 연한 테두리
          accent: '#00c6be',    // 실제 브런치 포인트 컬러 (민트)
        }
      },
      fontFamily: {
        sans: ['var(--font-sans)'],  // Noto Sans KR
      },
      fontSize: {
        'brunch-title': '27px',       // 브런치 대제목
        'brunch-subtitle': '17px',    // 브런치 중제목
        'brunch-body': '14px',        // 브런치 본문
      },
      letterSpacing: {
        'brunch-title': '-1px',       // 제목 자간
      },
      maxWidth: {
        'essay': '720px',             // 브런치 본문 최대 너비
      },
      spacing: {
        'brunch-section': '142px',    // 브런치 큰 섹션 여백
        'brunch-para': '41px',        // 브런치 단락 간격
      },
      boxShadow: {
        'brunch': '0 2px 8px rgba(0, 0, 0, 0.06)',
        'brunch-hover': '0 4px 16px rgba(0, 0, 0, 0.1)',
      }
    },
  },
  plugins: [],
};
export default config;
