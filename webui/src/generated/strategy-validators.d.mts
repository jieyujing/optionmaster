import type { StrategyPayoffRequestDTO, StrategyValuationRequestDTO, StrategyEnvelopeDTO } from './strategy-types';
export function validateStrategyPayoffRequest(value: unknown): value is StrategyPayoffRequestDTO;
export function validateStrategyValuationRequest(value: unknown): value is StrategyValuationRequestDTO;
export function validateStrategyEnvelope(value: unknown): value is StrategyEnvelopeDTO;
