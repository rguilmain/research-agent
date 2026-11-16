import { render, screen } from '@testing-library/react'
import { expect, it } from 'vitest'
import MarkdownView from './MarkdownView'

it('protects external links and does not load tracking images', () => {
  const { container } = render(<MarkdownView text={'[Evidence](https://example.org) ![tracking](https://tracker.test/pixel) <script>alert(1)</script>'} />)
  expect(screen.getByRole('link', { name: 'Evidence' })).toHaveAttribute('rel', 'noopener noreferrer nofollow')
  expect(container.querySelector('img')).toBeNull()
  expect(container.querySelector('script')).toBeNull()
})