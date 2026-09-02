'use client';

import { Card, CardContent } from '@/components/ui/card';
import { Skeleton } from '@/components/ui/skeleton';

export default function DashboardSkeleton() {
  return (
    <div className="space-y-4">
      {/* Top Stats Row Skeleton */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
        {Array.from({ length: 4 }).map((_, i) => (
          <Card key={i} className="bg-ub-surface border-ub-border rounded-lg">
            <CardContent className="p-4 space-y-3">
              <Skeleton className="h-4 w-28 bg-ub-border" />
              <Skeleton className="h-8 w-32 bg-ub-border" />
              <Skeleton className="h-3 w-20 bg-ub-border" />
            </CardContent>
          </Card>
        ))}
      </div>

      {/* Main Content Skeleton */}
      <div className="grid grid-cols-1 lg:grid-cols-5 gap-4">
        <div className="lg:col-span-3 space-y-4">
          <Card className="bg-ub-surface border-ub-border rounded-lg">
            <CardContent className="p-4 space-y-3">
              <Skeleton className="h-5 w-32 bg-ub-border" />
              {Array.from({ length: 4 }).map((_, i) => (
                <Skeleton key={i} className="h-4 w-full bg-ub-border" />
              ))}
            </CardContent>
          </Card>
          <Card className="bg-ub-surface border-ub-border rounded-lg">
            <CardContent className="p-4 space-y-3">
              <Skeleton className="h-5 w-36 bg-ub-border" />
              {Array.from({ length: 5 }).map((_, i) => (
                <Skeleton key={i} className="h-10 w-full bg-ub-border" />
              ))}
            </CardContent>
          </Card>
        </div>
        <div className="lg:col-span-2 space-y-4">
          {Array.from({ length: 4 }).map((_, i) => (
            <Card key={i} className="bg-ub-surface border-ub-border rounded-lg">
              <CardContent className="p-4 space-y-3">
                <Skeleton className="h-5 w-28 bg-ub-border" />
                <Skeleton className="h-4 w-20 bg-ub-border" />
              </CardContent>
            </Card>
          ))}
        </div>
      </div>
    </div>
  );
}
