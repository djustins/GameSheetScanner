import { createTheme, Table, type CSSVariablesResolver, type MantineColorsTuple } from '@mantine/core'

// Team Pittsburgh Ball Hockey's black/gold/white branding, matching the
// Streamlit app's THEME_COLORS (app.py): gold buttons and accents in both
// modes, black backgrounds in dark, light gray in light.
export const THEME_COLORS = {
  dark: {
    primary: '#FFC72C',
    heading: '#FFC72C',
    background: '#000000',
    secondaryBg: '#161616',
    text: '#F5F5F0',
    panelBg: '#242424',
  },
  light: {
    // Darker heading gold than the button gold: bright gold text on a light
    // background reads as washed out.
    primary: '#FFC72C',
    heading: '#8A6D1B',
    background: '#E9E9E9',
    secondaryBg: '#D6D6D6',
    text: '#000000',
    panelBg: '#DADADA',
  },
}

// Shade 4 is the brand gold; shade 8 is the light theme's heading gold.
const gold: MantineColorsTuple = [
  '#FFF9E1', '#FFF0C7', '#FFE49A', '#FFD666', '#FFC72C',
  '#F2B91E', '#D9A514', '#B5890F', '#8A6D1B', '#5E4A10',
]

// Mantine's dark surfaces are blue-tinted grays; these are neutral, stepping
// from the off-white text (0) down to the black page background (7).
const dark: MantineColorsTuple = [
  '#F5F5F0', '#C9C9C4', '#9A9A96', '#6E6E6B', '#3D3D3D',
  '#2E2E2E', '#242424', '#000000', '#161616', '#0A0A0A',
]

export const theme = createTheme({
  colors: { gold, dark },
  primaryColor: 'gold',
  primaryShade: 4,
  // Black text on the gold buttons, as on the Streamlit app.
  autoContrast: true,
  black: '#000000',
  components: {
    Table: Table.extend({ defaultProps: { stripedColor: 'var(--app-panel-bg)' } }),
  },
})

const appVariables = (c: (typeof THEME_COLORS)['dark']) => ({
  '--mantine-color-body': c.background,
  '--mantine-color-text': c.text,
  '--mantine-color-anchor': c.heading,
  '--app-heading': c.heading,
  '--app-secondary-bg': c.secondaryBg,
  '--app-panel-bg': c.panelBg,
})

export const cssVariablesResolver: CSSVariablesResolver = () => ({
  variables: {},
  dark: {
    ...appVariables(THEME_COLORS.dark),
    // Gold, not Mantine's near-white tint, for light/subtle/outline buttons.
    '--mantine-color-gold-light-color': THEME_COLORS.dark.primary,
    '--mantine-color-gold-outline': THEME_COLORS.dark.primary,
  },
  light: {
    ...appVariables(THEME_COLORS.light),
    // Mantine's default dimmed gray is too faint on the gray page background.
    '--mantine-color-dimmed': '#555555',
    // Gold text (light/subtle/outline buttons, the active nav link) needs the
    // darker gold to be readable on a light background.
    '--mantine-color-gold-light-color': THEME_COLORS.light.heading,
    '--mantine-color-gold-outline': THEME_COLORS.light.heading,
    '--mantine-color-gold-light': 'rgba(255, 199, 44, 0.3)',
    '--mantine-color-gold-light-hover': 'rgba(255, 199, 44, 0.45)',
  },
})
