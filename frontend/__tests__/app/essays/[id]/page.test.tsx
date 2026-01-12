/**
 * 에세이 상세 페이지 테스트
 *
 * 테스트 대상:
 * - 에세이 상세 정보 렌더링
 * - 뒤로 가기 링크
 * - Reason, Outline, 출처 섹션
 */

import { render, screen } from '@testing-library/react'
import EssayDetailPage from '@/app/essays/[id]/page'
import type { Essay } from '@/lib/types'

// Mock the API module
jest.mock('@/lib/api', () => ({
  fetchEssayById: jest.fn(),
}))

import { fetchEssayById } from '@/lib/api'

describe('EssayDetailPage', () => {
  const mockFetchEssayById = fetchEssayById as jest.MockedFunction<
    typeof fetchEssayById
  >

  const mockEssay: Essay = {
    id: 42,
    type: 'essay',
    title: 'The Philosophy of Testing',
    outline: [
      '1단: Understanding the foundations',
      '2단: Applying the principles',
      '3단: Mastering the craft',
    ],
    used_thoughts: [
      {
        thought_id: 1,
        claim: 'Testing is essential for software quality',
        source_title: 'Clean Code Principles',
        source_url: 'https://notion.so/clean-code',
      },
      {
        thought_id: 2,
        claim: 'Good tests serve as documentation',
        source_title: 'Test-Driven Development Guide',
        source_url: 'https://notion.so/tdd-guide',
      },
      {
        thought_id: 3,
        claim: 'Refactoring requires a safety net',
        source_title: 'Refactoring Best Practices',
        source_url: 'https://notion.so/refactoring',
      },
    ],
    reason: 'This essay connects testing philosophy with practical application.',
    pair_id: 5,
    generated_at: '2026-01-12T10:30:00Z',
  }

  beforeEach(() => {
    jest.clearAllMocks()
  })

  it('should render essay title', async () => {
    mockFetchEssayById.mockResolvedValueOnce(mockEssay)

    const page = await EssayDetailPage({ params: { id: '42' } })
    render(page)

    const title = screen.getByRole('heading', {
      name: 'The Philosophy of Testing',
      level: 1,
    })
    expect(title).toBeInTheDocument()
    expect(title).toHaveClass('brunch-title', 'text-5xl')
  })

  it('should render back link to home', async () => {
    mockFetchEssayById.mockResolvedValueOnce(mockEssay)

    const page = await EssayDetailPage({ params: { id: '42' } })
    render(page)

    const backLink = screen.getByRole('link', { name: /목록으로/i })
    expect(backLink).toBeInTheDocument()
    expect(backLink).toHaveAttribute('href', '/')
  })

  it('should render formatted date in Korean', async () => {
    mockFetchEssayById.mockResolvedValueOnce(mockEssay)

    const page = await EssayDetailPage({ params: { id: '42' } })
    render(page)

    // Check for date element (Korean format: 2026년 1월 일)
    // Note: actual day may vary due to timezone conversion
    const dateElement = screen.getByText(/2026년 1월 \d+일/)
    expect(dateElement).toBeInTheDocument()
    expect(dateElement.tagName).toBe('TIME')
  })

  it('should render reason section', async () => {
    mockFetchEssayById.mockResolvedValueOnce(mockEssay)

    const page = await EssayDetailPage({ params: { id: '42' } })
    render(page)

    expect(screen.getByText('왜 이 글감인가?')).toBeInTheDocument()
    expect(
      screen.getByText(/This essay connects testing philosophy/i)
    ).toBeInTheDocument()
  })

  it('should render outline section with all items', async () => {
    mockFetchEssayById.mockResolvedValueOnce(mockEssay)

    const page = await EssayDetailPage({ params: { id: '42' } })
    render(page)

    expect(screen.getByText('글의 구조')).toBeInTheDocument()
    expect(screen.getByText('1단: Understanding the foundations')).toBeInTheDocument()
    expect(screen.getByText('2단: Applying the principles')).toBeInTheDocument()
    expect(screen.getByText('3단: Mastering the craft')).toBeInTheDocument()
  })

  it('should render related thoughts with outline items', async () => {
    mockFetchEssayById.mockResolvedValueOnce(mockEssay)

    const page = await EssayDetailPage({ params: { id: '42' } })
    render(page)

    expect(
      screen.getByText('Testing is essential for software quality')
    ).toBeInTheDocument()
    expect(
      screen.getByText('Good tests serve as documentation')
    ).toBeInTheDocument()
    expect(
      screen.getByText('Refactoring requires a safety net')
    ).toBeInTheDocument()
  })

  it('should render sources section with all links', async () => {
    mockFetchEssayById.mockResolvedValueOnce(mockEssay)

    const page = await EssayDetailPage({ params: { id: '42' } })
    render(page)

    expect(screen.getByText('이 글감의 출처')).toBeInTheDocument()

    const links = screen.getAllByRole('link', { name: /→/ })
    expect(links).toHaveLength(3)

    expect(screen.getByText('Clean Code Principles')).toBeInTheDocument()
    expect(screen.getByText('Test-Driven Development Guide')).toBeInTheDocument()
    expect(screen.getByText('Refactoring Best Practices')).toBeInTheDocument()
  })

  it('should have correct source URLs with target blank', async () => {
    mockFetchEssayById.mockResolvedValueOnce(mockEssay)

    const page = await EssayDetailPage({ params: { id: '42' } })
    render(page)

    const sourceLinks = screen.getAllByRole('link').filter((link) =>
      link.getAttribute('href')?.startsWith('https://notion.so')
    )

    expect(sourceLinks).toHaveLength(3)

    sourceLinks.forEach((link) => {
      expect(link).toHaveAttribute('target', '_blank')
      expect(link).toHaveAttribute('rel', 'noopener noreferrer')
    })
  })

  it('should fetch essay with correct ID', async () => {
    mockFetchEssayById.mockResolvedValueOnce(mockEssay)

    await EssayDetailPage({ params: { id: '42' } })

    expect(mockFetchEssayById).toHaveBeenCalledWith(42)
    expect(mockFetchEssayById).toHaveBeenCalledTimes(1)
  })

  it('should handle different essay IDs', async () => {
    const essay1 = { ...mockEssay, id: 1 }
    const essay2 = { ...mockEssay, id: 999 }

    mockFetchEssayById.mockResolvedValueOnce(essay1)
    await EssayDetailPage({ params: { id: '1' } })
    expect(mockFetchEssayById).toHaveBeenCalledWith(1)

    mockFetchEssayById.mockResolvedValueOnce(essay2)
    await EssayDetailPage({ params: { id: '999' } })
    expect(mockFetchEssayById).toHaveBeenCalledWith(999)
  })

  it('should render all sections in correct order', async () => {
    mockFetchEssayById.mockResolvedValueOnce(mockEssay)

    const page = await EssayDetailPage({ params: { id: '42' } })
    const { container } = render(page)

    const sections = container.querySelectorAll('section')
    expect(sections).toHaveLength(3)

    // Check section headings order
    const headings = screen.getAllByRole('heading', { level: 2 })
    expect(headings[0]).toHaveTextContent('왜 이 글감인가?')
    expect(headings[1]).toHaveTextContent('글의 구조')
    expect(headings[2]).toHaveTextContent('이 글감의 출처')
  })

  it('should handle essay with minimal thoughts', async () => {
    const minimalEssay: Essay = {
      ...mockEssay,
      used_thoughts: [
        {
          thought_id: 1,
          claim: 'Single thought',
          source_title: 'Single Source',
          source_url: 'https://notion.so/single',
        },
      ],
      outline: ['1단: Only one', '2단: Another one', '3단: Last one'],
    }

    mockFetchEssayById.mockResolvedValueOnce(minimalEssay)

    const page = await EssayDetailPage({ params: { id: '42' } })
    render(page)

    expect(screen.getByText('Single thought')).toBeInTheDocument()
    expect(screen.getByText('Single Source')).toBeInTheDocument()

    // Outline items should still render even if no matching thought
    expect(screen.getByText('1단: Only one')).toBeInTheDocument()
    expect(screen.getByText('2단: Another one')).toBeInTheDocument()
    expect(screen.getByText('3단: Last one')).toBeInTheDocument()
  })

  it('should render article element', async () => {
    mockFetchEssayById.mockResolvedValueOnce(mockEssay)

    const page = await EssayDetailPage({ params: { id: '42' } })
    render(page)

    const article = screen.getByRole('article')
    expect(article).toBeInTheDocument()
  })
})
