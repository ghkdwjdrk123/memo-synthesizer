# Frontend Tests

This directory contains all tests for the Next.js 14 frontend application.

## Test Structure

```
__tests__/
├── lib/
│   └── api.test.ts                    # API client tests
├── components/
│   ├── Header.test.tsx                # Header component tests
│   └── EssayCard.test.tsx             # EssayCard component tests
└── app/
    ├── page.test.tsx                  # Main page tests
    └── essays/[id]/page.test.tsx      # Essay detail page tests
```

## Running Tests

```bash
# Run all tests
npm test

# Run tests in watch mode
npm run test:watch

# Run tests with coverage
npm run test:coverage
```

## Writing Tests

### Component Tests

```typescript
import { render, screen } from '@testing-library/react'
import MyComponent from '@/components/MyComponent'

describe('MyComponent', () => {
  it('should render correctly', () => {
    render(<MyComponent />)
    expect(screen.getByText('Expected Text')).toBeInTheDocument()
  })
})
```

### Server Component Tests (Next.js 14)

```typescript
import MyPage from '@/app/my-page/page'

describe('MyPage', () => {
  it('should render page', async () => {
    const page = await MyPage()
    render(page)
    expect(screen.getByRole('heading')).toBeInTheDocument()
  })
})
```

### API Tests

```typescript
import { myApiFunction } from '@/lib/api'

describe('myApiFunction', () => {
  beforeEach(() => {
    global.fetch = jest.fn()
  })

  it('should fetch data', async () => {
    (global.fetch as jest.Mock).mockResolvedValueOnce({
      ok: true,
      json: async () => ({ data: 'test' }),
    })

    const result = await myApiFunction()
    expect(result).toEqual({ data: 'test' })
  })
})
```

## Test Patterns

### 1. Mock API Calls

```typescript
jest.mock('@/lib/api', () => ({
  fetchEssays: jest.fn(),
}))
```

### 2. Mock Child Components

```typescript
jest.mock('@/components/MyComponent', () => {
  return function MyComponent() {
    return <div data-testid="my-component">Mocked</div>
  }
})
```

### 3. Test User Interactions

```typescript
import userEvent from '@testing-library/user-event'

const user = userEvent.setup()
await user.click(screen.getByRole('button'))
```

## Coverage Goals

- Statement Coverage: > 80%
- Branch Coverage: > 80%
- Function Coverage: > 90%
- Line Coverage: > 80%

Current coverage: **85% statements, 87.5% branches, 92.3% functions**

## Best Practices

1. Use semantic queries (`getByRole`, `getByLabelText`, `getByText`)
2. Test behavior, not implementation
3. Mock external dependencies
4. Test edge cases (empty states, errors)
5. Keep tests focused and isolated
6. Use descriptive test names
