import Link from 'next/link';
import type { Essay } from '@/lib/types';

interface EssayCardProps {
  essay: Essay;
}

export default function EssayCard({ essay }: EssayCardProps) {
  const formattedDate = new Date(essay.generated_at).toLocaleDateString('ko-KR', {
    year: 'numeric',
    month: 'long',
    day: 'numeric',
  });

  return (
    <Link href={`/essays/${essay.id}`} className="block">
      <article className="brunch-card cursor-pointer">
        {/* 제목 */}
        <h2 className="brunch-title text-3xl mb-4 text-brunch-text">
          {essay.title}
        </h2>

        {/* 이유 */}
        <p className="text-brunch-textLight text-base mb-6 line-clamp-2">
          {essay.reason}
        </p>

        {/* Outline 미리보기 */}
        <div className="space-y-2 mb-6">
          {essay.outline.map((item, index) => (
            <p key={index} className="text-sm text-brunch-textLight">
              {item}
            </p>
          ))}
        </div>

        {/* 출처 배지 */}
        <div className="flex flex-wrap gap-2 mb-4">
          {essay.used_thoughts_json.map((thought) => (
            <span
              key={thought.thought_id}
              className="inline-flex items-center gap-1 px-3 py-1 rounded-full bg-brunch-bg text-xs text-brunch-textLight"
            >
              {thought.source_title}
            </span>
          ))}
        </div>

        {/* 날짜 */}
        <time className="text-xs text-brunch-textLight">{formattedDate}</time>
      </article>
    </Link>
  );
}
