import { fetchEssayById } from '@/lib/api';
import Link from 'next/link';

export default async function EssayDetailPage({
  params,
}: {
  params: { id: string };
}) {
  const essay = await fetchEssayById(Number(params.id));

  const formattedDate = new Date(essay.generated_at).toLocaleDateString('ko-KR', {
    year: 'numeric',
    month: 'long',
    day: 'numeric',
  });

  return (
    <main className="min-h-screen pt-24 pb-16">
      <article className="max-w-essay mx-auto px-6">
        {/* 뒤로 가기 */}
        <Link
          href="/"
          className="inline-block mb-8 text-brunch-textLight hover:text-brunch-text"
        >
          목록으로
        </Link>

        {/* 제목 */}
        <h1 className="brunch-title text-5xl mb-4">{essay.title}</h1>

        {/* 날짜 */}
        <time className="block mb-12 text-brunch-textLight">
          {formattedDate}
        </time>

        <hr className="border-brunch-border mb-12" />

        {/* 이유 섹션 */}
        <section className="mb-16">
          <h2 className="text-2xl font-bold mb-4">왜 이 글감인가?</h2>
          <div className="bg-gray-50 p-6 rounded-lg border-l-4 border-brunch-accent">
            <p className="text-lg leading-relaxed">{essay.reason}</p>
          </div>
        </section>

        <hr className="border-brunch-border mb-12" />

        {/* 글의 구조 */}
        <section className="mb-16">
          <h2 className="text-2xl font-bold mb-8">글의 구조</h2>

          <div className="space-y-12">
            {essay.outline.map((outlineItem, index) => {
              const relatedThought = essay.used_thoughts_json[index];

              return (
                <div key={index}>
                  <h3 className="text-xl font-bold mb-4 text-brunch-accent">
                    {outlineItem}
                  </h3>
                  {relatedThought && (
                    <p className="text-lg leading-relaxed text-brunch-textMedium">
                      {relatedThought.claim}
                    </p>
                  )}
                </div>
              );
            })}
          </div>
        </section>

        <hr className="border-brunch-border mb-12" />

        {/* 출처 */}
        <section>
          <h2 className="text-2xl font-bold mb-6">이 글감의 출처</h2>
          <div className="space-y-3">
            {essay.used_thoughts_json.map((thought) => (
              <a
                key={thought.thought_id}
                href={thought.source_url}
                target="_blank"
                rel="noopener noreferrer"
                className="block p-4 bg-white border border-brunch-border rounded-lg hover:border-brunch-accent hover:shadow-brunch transition-all"
              >
                <span className="text-brunch-text font-medium">
                  {thought.source_title}
                </span>
                <span className="ml-2 text-brunch-textLight">→</span>
              </a>
            ))}
          </div>
        </section>
      </article>
    </main>
  );
}
