import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { Toaster } from 'react-hot-toast'
import App from './App'
import './index.css'

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 30_000,
      refetchOnWindowFocus: false,
      retry: (failureCount, error) => {
        // Don't retry 401 errors
        if (error instanceof Error && 'status' in error && (error as { status: number }).status === 401) {
          return false
        }
        return failureCount < 2
      },
    },
  },
})

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <App />
      <Toaster
        position="bottom-right"
        toastOptions={{
          duration: 4000,
          style: {
            background: 'hsl(204 25% 10%)',
            color: 'hsl(188 29% 95%)',
            border: '1px solid hsl(198 22% 20%)',
            borderRadius: '0.5rem',
            fontSize: '0.875rem',
          },
          success: {
            iconTheme: { primary: 'hsl(193 45% 70%)', secondary: 'hsl(204 25% 10%)' },
          },
          error: {
            iconTheme: { primary: 'hsl(0 70% 55%)', secondary: 'hsl(204 25% 10%)' },
          },
        }}
      />
    </QueryClientProvider>
  </StrictMode>,
)
