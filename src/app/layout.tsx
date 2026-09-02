import './globals.css';
import type { Metadata } from 'next';
import { Inter } from 'next/font/google';
import Providers from '@/components/Providers';

const inter = Inter({
  variable: '--font-inter',
  subsets: ['latin'],
  display: 'swap',
});

export const metadata: Metadata = {
  title: 'UltraBot Web | High-Precision Automated Trading System',
  description: 'Multi-broker autonomous trading platform with real-time risk management, Kronos AI validation, and multi-strategy execution.',
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en" className="dark" suppressHydrationWarning>
      <body
        className={`${inter.variable} font-sans antialiased`}
        style={{ backgroundColor: '#0a0e17', color: '#f1f5f9' }}
      >
        <Providers>{children}</Providers>
      </body>
    </html>
  );
}
