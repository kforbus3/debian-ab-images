import { Card, PageHeader } from "../components/ui";
import OverlayManager from "../components/OverlayManager";

export default function Files() {
  return (
    <div>
      <PageHeader title="Image Files"
                  subtitle="Your own files, copied into every image built here" />
      <div className="grid grid-cols-1 gap-6 lg:grid-cols-[minmax(0,2fr)_minmax(0,1fr)]">
        <Card className="p-5">
          <OverlayManager />
        </Card>
        <Card className="h-fit p-5 text-sm text-zinc-400">
          <h2 className="mb-2 text-sm font-semibold text-zinc-200">How these files behave</h2>
          <p>
            Each file is copied over the image's root filesystem, keeping its path:
            <code className="mx-1 text-zinc-300">/etc/hosts</code> here is
            <code className="mx-1 text-zinc-300">/etc/hosts</code> on every machine imaged
            from it. They are applied <em>after</em> the project's own overlay, so your
            version of a file wins over the default this repo ships.
          </p>
          <h3 className="mb-1 mt-4 text-xs font-semibold uppercase tracking-wide text-zinc-500">
            They also override the machine
          </h3>
          <p>
            A deployed machine's root filesystem is an overlay, so a file it has written
            at the same path would normally shadow the image's copy — an update would
            install your <code className="text-zinc-300">/etc/hosts</code> and the machine
            would carry on reading its own, with nothing to say so. Every file here is
            recorded as image-owned, which drops the machine's copy at that exact path on
            the update that delivers yours. Other files in the same directory are
            untouched, so shipping one netplan file does not remove the machine's others.
          </p>
          <h3 className="mb-1 mt-4 text-xs font-semibold uppercase tracking-wide text-zinc-500">
            Permissions are part of the file
          </h3>
          <p>
            The mode is preserved into the image, so a script shipped
            <code className="mx-1 text-zinc-300">0644</code> is a script that does not run
            on the machine. Use the mode button in the list, or set it explicitly when
            editing.
          </p>
          <h3 className="mb-1 mt-4 text-xs font-semibold uppercase tracking-wide text-zinc-500">
            What does not belong here
          </h3>
          <p>
            Per-machine identity — hostnames, SSH host keys, machine-id — is generated on
            first boot and lives in the overlay on purpose. This is for fleet-wide
            configuration that should be part of the image and replaced by every update.
          </p>
          <p className="mt-4 text-xs text-zinc-500">
            Nothing here is committed to git: the directory is ignored except for its
            README, so site-specific configuration stays local to this checkout.
          </p>
        </Card>
      </div>
    </div>
  );
}
