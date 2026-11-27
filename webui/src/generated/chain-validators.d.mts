import type { ChainApiEnvelope, ChainValuationRequestDTO } from './chain-types';
export function validateChainEnvelope(value: unknown): value is ChainApiEnvelope;
export function validateChainRequest(value: unknown): value is ChainValuationRequestDTO;
