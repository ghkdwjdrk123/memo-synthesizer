/**
 * Header 컴포넌트 테스트
 *
 * 테스트 대상:
 * - 컴포넌트 렌더링
 * - 로고 텍스트 표시
 * - 홈 링크 동작
 */

import { render, screen } from '@testing-library/react'
import Header from '@/components/Header'

describe('Header Component', () => {
  it('should render header with logo', () => {
    render(<Header />)

    const logoLink = screen.getByRole('link', { name: /Essay Garden/i })
    expect(logoLink).toBeInTheDocument()
  })

  it('should have correct link to home page', () => {
    render(<Header />)

    const logoLink = screen.getByRole('link', { name: /Essay Garden/i })
    expect(logoLink).toHaveAttribute('href', '/')
  })

  it('should have correct styling classes', () => {
    render(<Header />)

    const header = screen.getByRole('banner')
    expect(header).toHaveClass('fixed', 'top-0', 'left-0', 'right-0', 'z-50')
  })

  it('should render logo text correctly', () => {
    render(<Header />)

    const logoText = screen.getByText('Essay Garden')
    expect(logoText).toBeInTheDocument()
    expect(logoText).toHaveClass('text-xl', 'font-medium')
  })
})
