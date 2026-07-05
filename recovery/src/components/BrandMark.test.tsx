/**
 * BrandMark component tests.
 *
 * Verifies that the MSM logo renders in the header with the Design-DNA
 * styling pattern.
 */

// @vitest-environment jsdom

import { describe, it, expect, afterEach } from 'vitest';
import { render, screen, cleanup } from '@testing-library/react';
import { BrandMark } from './BrandMark';

afterEach(() => {
  cleanup();
});

describe('BrandMark', () => {
  it('renders a logo image', () => {
    render(<BrandMark />);
    const mark = screen.getByTestId('brand-mark');
    expect(mark).toBeDefined();
    const img = mark.querySelector('img');
    expect(img).not.toBeNull();
    expect(img?.getAttribute('src')).toBe('/msm-logo.png');
    expect(img?.getAttribute('alt')).toBe('MSM Logo');
  });

  it('uses the Design-DNA rounded container styling', () => {
    render(<BrandMark />);
    const mark = screen.getByTestId('brand-mark');
    expect(mark.className).toMatch(/rounded-full/);
    expect(mark.className).toMatch(/border/);
  });

  it('shows status dot when status=true', () => {
    render(<BrandMark status />);
    const mark = screen.getByTestId('brand-mark');
    const dot = mark.querySelector('[aria-hidden="true"]');
    expect(dot).not.toBeNull();
    expect(dot?.className).toMatch(/rounded-full/);
  });

  it('hides status dot when status=false', () => {
    render(<BrandMark status={false} />);
    const mark = screen.getByTestId('brand-mark');
    // No status dot should be present
    const dots = mark.querySelectorAll('[aria-hidden="true"]');
    expect(dots.length).toBe(0);
  });

  it('accepts a custom logo source', () => {
    render(<BrandMark logoSrc="/custom-logo.png" />);
    const img = screen.getByTestId('brand-mark').querySelector('img');
    expect(img?.getAttribute('src')).toBe('/custom-logo.png');
  });
});
