import Link from 'next/link';

export default function Header() {
  return (
    <header className="fixed top-0 left-0 right-0 z-50 bg-white/80 backdrop-blur-sm border-b border-brunch-border">
      <div className="max-w-4xl mx-auto px-6 py-4">
        <Link
          href="/"
          className="text-xl font-medium text-brunch-text hover:text-brunch-accent transition-colors"
        >
          Essay Garden
        </Link>
      </div>
    </header>
  );
}
