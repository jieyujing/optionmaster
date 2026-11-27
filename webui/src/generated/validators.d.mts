import type { ApiEnvelope, ValuationRequestDTO } from './types';
export function validateEnvelope(value: unknown): value is ApiEnvelope;
export function validateRequest(value: unknown): value is ValuationRequestDTO;
