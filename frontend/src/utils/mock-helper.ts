import type { MockLevel } from '../types';

export function mockLabel(level: MockLevel): string {
  const map: Record<MockLevel, string> = {
    mock: 'Mock 数据',
    placeholder: '占位',
    not_connected: '未接真实服务',
    future: '规划中',
  };
  return map[level];
}

export function mockClass(level: MockLevel): string {
  const map: Record<MockLevel, string> = {
    mock: 'mock-tag',
    placeholder: 'placeholder-tag',
    not_connected: 'not-connected-tag',
    future: 'future-tag',
  };
  return map[level];
}

export function isReal(_level: MockLevel): boolean {
  return false; // All R3 data is mock
}
