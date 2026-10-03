import 'vuetify/styles'
import { createVuetify } from 'vuetify'
import {
  VApp, VMain, VContainer, VRow, VCol,
  VBtn, VBtnToggle, VTextField, VCard, VCardTitle, VCardItem,
  VCardText, VCardActions, VForm, VChip, VFileInput, VCheckbox,
  VDataTable, VIcon, VDivider, VAvatar, VSnackbar, VSheet, VSlider, VRangeSlider, VDatePicker, VSelect, VMenu, VAlert, VDialog, VProgressCircular, VProgressLinear, VSwitch,
  VNavigationDrawer, VList, VListItem, VListItemTitle, VListItemSubtitle, VImg, VExpansionPanels, VExpansionPanel, VExpansionPanelText,
  VAppBar, VAppBarTitle, VToolbar, VToolbarTitle, VSpacer, VFooter, VTooltip, VListSubheader, VOverlay, VVirtualScroll, VBadge,
   VTable, VChipGroup, VTabs, VTab, VWindow, VWindowItem
} from 'vuetify/components'
import * as directives from 'vuetify/directives'
import { aliases, mdi } from 'vuetify/iconsets/mdi'
import { h } from 'vue'
import ParkVisIcon from '../components/ParkVisIcon.vue'

const custom = {
  component: (props) => h(ParkVisIcon, props)
}

export default createVuetify({
  theme: {
    defaultTheme: 'parkingVis',
    themes: { parkingVis: { dark: false, colors: {
      background: '#EEE8DB', surface: '#FAF6ED', primary: '#59613B', secondary: '#A85E36',
      success: '#617749', error: '#AD5140', warning: '#AC802E', info: '#6B7B83',
      'on-background': '#39372B', 'on-surface': '#39372B',
    } } },
  },
  defaults: { VBtn: { rounded: 0, elevation: 0 }, VCard: { rounded: 0, elevation: 0 } },
  components: {
    VApp,
    VMain,
    VContainer,
    VRow,
    VCol,
    VBtn,
    VBtnToggle,
    VTextField,
    VCard,
    VCardTitle,
    VCardItem,
    VCardText,
    VCardActions,
    VForm,
    VFileInput,
    VNavigationDrawer,
    VList,
    VListItem,
    VListItemTitle,
    VListItemSubtitle,
    VImg,
    VAppBar,
    VAppBarTitle,
    VToolbar,
    VToolbarTitle,
    VDataTable,
    VIcon,
    VDivider,
    VChip,
    VAvatar,
    VCheckbox,
    VSnackbar,
    VSpacer,
    VSheet,
    VSlider,
    VRangeSlider,
    VDatePicker,
    VSelect,
    VMenu,
    VAlert,
    VDialog,
    VProgressCircular,
    VProgressLinear,
    VSwitch,
    VExpansionPanels,
    VExpansionPanel,
    VExpansionPanelText,
    VFooter,
    VTooltip,
    VListSubheader,
    VOverlay,
    VVirtualScroll,
    VBadge,
    VTabs,
    VTab,
    VWindow,
    VWindowItem,
    VTable,
    VChipGroup
  },
  directives,
  icons: {
    defaultSet: 'mdi',
    aliases: {
      ...aliases,
      'park-vis': custom
    },
    sets: { 
      mdi,
      custom
    }
  }
})
