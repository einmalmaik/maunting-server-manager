// Vitest global setup. @testing-library/jest-dom matchers are imported so
// component tests (added in the `recovery-basic-ui` feature) get `toBeInTheDocument`
// etc. The decrypt-logic tests run in the node environment and do not need DOM
// matchers, but importing them here is harmless.
import '@testing-library/jest-dom';
