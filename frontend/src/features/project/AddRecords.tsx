import { useId, useState, type FormEvent } from "react";
import { MapPin, Plus, Users } from "lucide-react";
import type { Asset } from "../generation/client";
import { direction } from "./client";
import type { DirectController } from "./use-direction";

// Round 1 of the blind usability campaign (T1-F1): a newcomer found no way to put characters or places into a movie.
// These are the Project panel's own add forms; Gaia and the Library remain the other ways in.

/** Adds a character to the movie's cast: a name, who they are, what must stay the same, and an identity picture. */
export function AddCast({
  d,
  images,
}: {
  d: DirectController;
  images: Asset[];
}) {
  const id = useId();
  const movie = d.movie.data;
  const [open, setOpen] = useState(false);
  const [name, setName] = useState("");
  const [about, setAbout] = useState("");
  const [keeps, setKeeps] = useState("");
  const [picture, setPicture] = useState("");
  if (!movie) return null;
  async function submit(e: FormEvent) {
    e.preventDefault();
    const added = await d.perform(
      () =>
        direction.addSubject(movie!.id, {
          expected_revision: movie!.revision,
          name: name.trim(),
          description: about.trim(),
          keeps: keeps
            .split(",")
            .map((k) => k.trim())
            .filter(Boolean),
          identity_asset_id: picture || null,
        }),
      "Character added",
    );
    if (added) {
      setName("");
      setAbout("");
      setKeeps("");
      setPicture("");
      setOpen(false);
    }
  }
  return open ? (
    <form className="project-add" onSubmit={(e) => void submit(e)}>
      <label htmlFor={`${id}-name`}>Name</label>
      <input
        id={`${id}-name`}
        value={name}
        autoFocus
        required
        maxLength={120}
        onChange={(e) => setName(e.target.value)}
        placeholder="Lena"
      />
      <label htmlFor={`${id}-about`}>Who they are</label>
      <input
        id={`${id}-about`}
        value={about}
        maxLength={2000}
        onChange={(e) => setAbout(e.target.value)}
        placeholder="A bicycle courier in her thirties"
      />
      <label htmlFor={`${id}-keeps`}>Always the same (comma-separated)</label>
      <input
        id={`${id}-keeps`}
        value={keeps}
        onChange={(e) => setKeeps(e.target.value)}
        placeholder="yellow rain jacket, courier bag"
      />
      <label htmlFor={`${id}-picture`}>Identity picture</label>
      <select
        id={`${id}-picture`}
        value={picture}
        onChange={(e) => setPicture(e.target.value)}
      >
        <option value="">None yet</option>
        {images.map((image) => (
          <option key={image.id} value={image.id}>
            {image.name}
          </option>
        ))}
      </select>
      <div className="project-add-actions">
        <button type="button" onClick={() => setOpen(false)}>
          Cancel
        </button>
        <button
          type="submit"
          className="primary-button"
          disabled={!name.trim() || d.busy}
        >
          Add to the cast
        </button>
      </div>
    </form>
  ) : (
    <button className="project-add-button" onClick={() => setOpen(true)}>
      <Users size={14} aria-hidden="true" />
      <Plus size={12} aria-hidden="true" /> Add a character…
    </button>
  );
}

/** Adds a place the movie's segments can open in, with its first camera angle (more angles come in Prepare). */
export function AddPlace({ d }: { d: DirectController }) {
  const id = useId();
  const [open, setOpen] = useState(false);
  const [name, setName] = useState("");
  const [angle, setAngle] = useState("Wide");
  async function submit(e: FormEvent) {
    e.preventDefault();
    const added = await d.perform(async () => {
      const place = await direction.createPlace({ name: name.trim() });
      await direction.addAngle(place.id, { name: angle.trim() || "Wide" });
      return place;
    }, "Place added");
    if (added) {
      setName("");
      setAngle("Wide");
      setOpen(false);
    }
  }
  return open ? (
    <form className="project-add" onSubmit={(e) => void submit(e)}>
      <label htmlFor={`${id}-name`}>Place</label>
      <input
        id={`${id}-name`}
        value={name}
        autoFocus
        required
        maxLength={120}
        onChange={(e) => setName(e.target.value)}
        placeholder="The bookshop"
      />
      <label htmlFor={`${id}-angle`}>First camera angle</label>
      <input
        id={`${id}-angle`}
        value={angle}
        maxLength={120}
        onChange={(e) => setAngle(e.target.value)}
      />
      <div className="project-add-actions">
        <button type="button" onClick={() => setOpen(false)}>
          Cancel
        </button>
        <button
          type="submit"
          className="primary-button"
          disabled={!name.trim() || d.busy}
        >
          Add the place
        </button>
      </div>
    </form>
  ) : (
    <button className="project-add-button" onClick={() => setOpen(true)}>
      <MapPin size={14} aria-hidden="true" />
      <Plus size={12} aria-hidden="true" /> Add a place…
    </button>
  );
}
