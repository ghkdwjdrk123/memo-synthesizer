/**
 * EssayCard 컴포넌트 테스트
 *
 * 테스트 대상:
 * - 에세이 데이터 렌더링 (제목, reason, outline, 출처, 날짜)
 * - 링크 동작
 * - 날짜 포맷팅
 */

import { render, screen } from '@testing-library/react'
import EssayCard from '@/components/EssayCard'
import type { Essay } from '@/lib/types'

describe('EssayCard Component', () => {
  const mockEssay: Essay = {
    id: 1,
    type: 'essay',
    title: 'The Art of Testing',
    outline: [
      '1단: Why testing matters',
      '2단: Best practices',
      '3단: Common pitfalls',
    ],
    used_thoughts: [
      {
        thought_id: 1,
        claim: 'Testing improves code quality',
        source_title: 'Software Engineering Handbook',
        source_url: 'https://notion.so/handbook',
      },
      {
        thought_id: 2,
        claim: 'TDD leads to better design',
        source_title: 'Test-Driven Development',
        source_url: 'https://notion.so/tdd',
      },
    ],
    reason: 'This essay explores the importance of testing in modern software development.',
    pair_id: 1,
    generated_at: '2026-01-12T15:30:00Z',
  }

  it('should render essay title', () => {
    render(<EssayCard essay={mockEssay} />)

    const title = screen.getByRole('heading', { name: 'The Art of Testing' })
    expect(title).toBeInTheDocument()
    expect(title).toHaveClass('brunch-title', 'text-3xl')
  })

  it('should render essay reason', () => {
    render(<EssayCard essay={mockEssay} />)

    const reason = screen.getByText(
      /This essay explores the importance of testing/i
    )
    expect(reason).toBeInTheDocument()
  })

  it('should render all outline items', () => {
    render(<EssayCard essay={mockEssay} />)

    expect(screen.getByText('1단: Why testing matters')).toBeInTheDocument()
    expect(screen.getByText('2단: Best practices')).toBeInTheDocument()
    expect(screen.getByText('3단: Common pitfalls')).toBeInTheDocument()
  })

  it('should render all source titles as badges', () => {
    render(<EssayCard essay={mockEssay} />)

    expect(screen.getByText('Software Engineering Handbook')).toBeInTheDocument()
    expect(screen.getByText('Test-Driven Development')).toBeInTheDocument()
  })

  it('should render formatted date in Korean', () => {
    render(<EssayCard essay={mockEssay} />)

    // Check for date element (Korean format: 2026년 1월 일)
    // Note: actual day may vary due to timezone conversion
    const dateElement = screen.getByText(/2026년 1월 \d+일/)
    expect(dateElement).toBeInTheDocument()
    expect(dateElement.tagName).toBe('TIME')
  })

  it('should have correct link to essay detail page', () => {
    render(<EssayCard essay={mockEssay} />)

    const link = screen.getByRole('link')
    expect(link).toHaveAttribute('href', '/essays/1')
  })

  it('should render as an article element', () => {
    render(<EssayCard essay={mockEssay} />)

    const article = screen.getByRole('article')
    expect(article).toBeInTheDocument()
    expect(article).toHaveClass('brunch-card', 'cursor-pointer')
  })

  it('should handle single outline item correctly', () => {
    const essayWithSingleOutline = {
      ...mockEssay,
      outline: ['Only one section'],
    }

    render(<EssayCard essay={essayWithSingleOutline} />)

    expect(screen.getByText('Only one section')).toBeInTheDocument()
  })

  it('should handle single thought source', () => {
    const essayWithSingleThought = {
      ...mockEssay,
      used_thoughts: [
        {
          thought_id: 1,
          claim: 'Single claim',
          source_title: 'Single Source',
          source_url: 'https://notion.so/single',
        },
      ],
    }

    render(<EssayCard essay={essayWithSingleThought} />)

    expect(screen.getByText('Single Source')).toBeInTheDocument()
  })

  it('should handle long reason text with line-clamp', () => {
    const essayWithLongReason = {
      ...mockEssay,
      reason:
        'This is a very long reason that should be clamped. '.repeat(10),
    }

    render(<EssayCard essay={essayWithLongReason} />)

    const reasonElement = screen.getByText(/This is a very long reason/i)
    expect(reasonElement).toHaveClass('line-clamp-2')
  })

  it('should render different essay IDs correctly', () => {
    const essay1 = { ...mockEssay, id: 42 }
    const essay2 = { ...mockEssay, id: 100 }

    const { rerender } = render(<EssayCard essay={essay1} />)
    expect(screen.getByRole('link')).toHaveAttribute('href', '/essays/42')

    rerender(<EssayCard essay={essay2} />)
    expect(screen.getByRole('link')).toHaveAttribute('href', '/essays/100')
  })
})
