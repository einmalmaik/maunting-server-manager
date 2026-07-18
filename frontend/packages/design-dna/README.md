# @maunting/design-dna

Versioned, repository-contained Design-DNA boundary for MSM. It exports the
shared visual tokens and their CSS contract. MSM owns interactive components in
`frontend/src/Singra/UI`, which re-exports the established panel primitives.
This keeps one accessibility and behavior implementation per component and does
not depend on the author's external Design-DNA workspace.
