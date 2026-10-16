// Source: assistant-ui/assistant-ui @ 2bdd534a3d8da45e6c7a9ee6553862b83713059d (MIT). See UPSTREAM.md.
import { cn } from "@/lib/utils";

function Skeleton({ className, ...props }: React.ComponentProps<"div">) {
  return (
    <div
      data-slot="skeleton"
      className={cn("bg-muted animate-pulse rounded-md", className)}
      {...props}
    />
  );
}

export { Skeleton };
