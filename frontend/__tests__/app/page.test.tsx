/**
 * 메인 페이지 (HomePage) 테스트
 *
 * 테스트 대상:
 * - 에세이 목록 렌더링
 * - 빈 상태 처리
 * - Header 포함 여부
 */

import { render, screen } from '@testing-library/react'
import HomePage from '@/app/page'
import type { EssayListResponse } from '@/lib/types'

// Mock the API module
jest.mock('@/lib/api', () => ({
  fetchEssays: jest.fn(),
}))

// Mock the components
jest.mock('@/components/Header', () => {
  return function Header() {
    return <header data-testid="header">Header Component</header>
  }
})

jest.mock('@/components/EssayCard', () => {
  return function EssayCard({ essay }: any) {
    return (
      <article data-testid={`essay-card-${essay.id}`}>
        <h2>{essay.title}</h2>
      </article>
    )
  }
})

import { fetchEssays } from '@/lib/api'

describe('HomePage', () => {
  const mockFetchEssays = fetchEssays as jest.MockedFunction<
    typeof fetchEssays
  >

  beforeEach(() => {
    jest.clearAllMocks()
  })

  it('should render header', async () => {
    const mockResponse: EssayListResponse = {
      total: 0,
      essays: [],
    }
    mockFetchEssays.mockResolvedValueOnce(mockResponse)

    const page = await HomePage()
    const { container } = render(page)

    expect(screen.getByTestId('header')).toBeInTheDocument()
  })

  it('should render page title', async () => {
    const mockResponse: EssayListResponse = {
      total: 0,
      essays: [],
    }
    mockFetchEssays.mockResolvedValueOnce(mockResponse)

    const page = await HomePage()
    render(page)

    const title = screen.getByRole('heading', { name: /Essay Garden/i, level: 1 })
    expect(title).toBeInTheDocument()
    expect(title).toHaveClass('brunch-title', 'text-5xl')
  })

  it('should display empty message when no essays', async () => {
    const mockResponse: EssayListResponse = {
      total: 0,
      essays: [],
    }
    mockFetchEssays.mockResolvedValueOnce(mockResponse)

    const page = await HomePage()
    render(page)

    expect(
      screen.getByText(/아직 생성된 에세이가 없습니다/i)
    ).toBeInTheDocument()
  })

  it('should render essay cards when essays exist', async () => {
    const mockResponse: EssayListResponse = {
      total: 2,
      essays: [
        {
          id: 1,
          type: 'essay',
          title: 'First Essay',
          outline: ['1', '2', '3'],
          used_thoughts: [],
          reason: 'First reason',
          pair_id: 1,
          generated_at: '2026-01-12T00:00:00Z',
        },
        {
          id: 2,
          type: 'essay',
          title: 'Second Essay',
          outline: ['1', '2', '3'],
          used_thoughts: [],
          reason: 'Second reason',
          pair_id: 2,
          generated_at: '2026-01-12T01:00:00Z',
        },
      ],
    }
    mockFetchEssays.mockResolvedValueOnce(mockResponse)

    const page = await HomePage()
    render(page)

    expect(screen.getByTestId('essay-card-1')).toBeInTheDocument()
    expect(screen.getByTestId('essay-card-2')).toBeInTheDocument()
    expect(screen.getByText('First Essay')).toBeInTheDocument()
    expect(screen.getByText('Second Essay')).toBeInTheDocument()
  })

  it('should not show empty message when essays exist', async () => {
    const mockResponse: EssayListResponse = {
      total: 1,
      essays: [
        {
          id: 1,
          type: 'essay',
          title: 'Test Essay',
          outline: ['1', '2', '3'],
          used_thoughts: [],
          reason: 'Test reason',
          pair_id: 1,
          generated_at: '2026-01-12T00:00:00Z',
        },
      ],
    }
    mockFetchEssays.mockResolvedValueOnce(mockResponse)

    const page = await HomePage()
    render(page)

    expect(
      screen.queryByText(/아직 생성된 에세이가 없습니다/i)
    ).not.toBeInTheDocument()
  })

  it('should fetch essays with correct parameters', async () => {
    const mockResponse: EssayListResponse = {
      total: 0,
      essays: [],
    }
    mockFetchEssays.mockResolvedValueOnce(mockResponse)

    await HomePage()

    expect(mockFetchEssays).toHaveBeenCalledWith(20, 0)
    expect(mockFetchEssays).toHaveBeenCalledTimes(1)
  })

  it('should render multiple essays in correct order', async () => {
    const mockResponse: EssayListResponse = {
      total: 3,
      essays: [
        {
          id: 10,
          type: 'essay',
          title: 'Essay Ten',
          outline: ['1', '2', '3'],
          used_thoughts: [],
          reason: 'Reason ten',
          pair_id: 10,
          generated_at: '2026-01-12T00:00:00Z',
        },
        {
          id: 20,
          type: 'essay',
          title: 'Essay Twenty',
          outline: ['1', '2', '3'],
          used_thoughts: [],
          reason: 'Reason twenty',
          pair_id: 20,
          generated_at: '2026-01-12T01:00:00Z',
        },
        {
          id: 30,
          type: 'essay',
          title: 'Essay Thirty',
          outline: ['1', '2', '3'],
          used_thoughts: [],
          reason: 'Reason thirty',
          pair_id: 30,
          generated_at: '2026-01-12T02:00:00Z',
        },
      ],
    }
    mockFetchEssays.mockResolvedValueOnce(mockResponse)

    const page = await HomePage()
    render(page)

    const cards = screen.getAllByTestId(/essay-card-/)
    expect(cards).toHaveLength(3)
    expect(cards[0]).toHaveAttribute('data-testid', 'essay-card-10')
    expect(cards[1]).toHaveAttribute('data-testid', 'essay-card-20')
    expect(cards[2]).toHaveAttribute('data-testid', 'essay-card-30')
  })
})
