import type { CatalogApiEnvelope, CatalogValuationRequestDTO } from './catalog-types';
export function validateCatalogEnvelope(value: unknown): value is CatalogApiEnvelope;
export function validateCatalogRequest(value: unknown): value is CatalogValuationRequestDTO;
