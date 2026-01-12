import { fetchEssays } from '@/lib/api';
import EssayCard from '@/components/EssayCard';
import Header from '@/components/Header';

export default async function HomePage() {
  const data = await fetchEssays(20, 0);

  return (
    <>
      <Header />
      <main className="min-h-screen pt-24 pb-16">
        <div className="max-w-4xl mx-auto px-6">
          <h1 className="brunch-title text-5xl mb-12 text-center">
            Essay Garden
          </h1>

          {data.essays.length === 0 ? (
            <p className="text-center text-brunch-textLight">
              아직 생성된 에세이가 없습니다.
            </p>
          ) : (
            <div className="space-y-8">
              {data.essays.map((essay) => (
                <EssayCard key={essay.id} essay={essay} />
              ))}
            </div>
          )}
        </div>
      </main>
    </>
  );
}
